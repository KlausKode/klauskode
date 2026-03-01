"""GitHub operations via the `gh` CLI.

The `gh` CLI is always available inside the Docker container.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from urllib.parse import quote

# Module-level verbosity level — kept for backwards compat but prefer passing
# verbose as a parameter to _run_gh() directly.
verbose = 0


@dataclass
class Issue:
    number: int
    title: str
    body: str
    labels: list[str]
    state: str = "open"


@dataclass
class Repository:
    full_name: str        # "owner/repo"
    description: str
    language: str
    stars: int
    open_issues_count: int
    topics: list[str]


def _run_gh(*args: str, check: bool = True, verbose: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run a `gh` CLI command.

    Args:
        verbose: Verbosity level. If None, falls back to module-level ``verbose``.
    """
    cmd = ["gh", *args]

    # Resolve verbosity: explicit parameter wins, else module-level fallback
    _verbose = verbose if verbose is not None else globals()["verbose"]

    if _verbose:
        print(f"  [gh] running: gh {' '.join(args)}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if _verbose:
        if result.stdout.strip():
            print(f"  [gh] stdout: {result.stdout.strip()[:500]}")
        if result.stderr.strip():
            print(f"  [gh] stderr: {result.stderr.strip()[:500]}")

    if check and result.returncode != 0:
        print(f"Error running gh {' '.join(args)}:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(1)
    return result


def check_gh_auth() -> bool:
    """Return True if we can authenticate with GitHub."""
    if not os.environ.get("GH_TOKEN"):
        return False
    result = _run_gh("auth", "status", check=False)
    return result.returncode == 0


def check_token_scopes() -> dict[str, bool]:
    """Check what permissions the current token has.

    Returns a dict of scope/capability -> bool.
    """
    result = _run_gh(
        "api", "user", "--include", "--jq", ".",
        check=False,
    )

    scopes: dict[str, bool] = {
        "authenticated": result.returncode == 0,
        "can_read_repos": False,
        "can_fork": False,
        "can_create_prs": False,
    }

    if result.returncode != 0:
        return scopes

    # For fine-grained PATs, check by actually trying key endpoints
    # Check repo read access
    read_result = _run_gh("api", "repos/octocat/Hello-World", "--jq", ".id", check=False)
    scopes["can_read_repos"] = read_result.returncode == 0

    # Check if we can list our own repos (implies write access)
    own_result = _run_gh("api", "user/repos", "--jq", "length", check=False)
    scopes["can_fork"] = own_result.returncode == 0

    # PR creation uses same permissions as fork + write
    scopes["can_create_prs"] = scopes["can_fork"]

    # Look for X-OAuth-Scopes header in stderr (classic tokens)
    for line in result.stderr.splitlines():
        if "X-Oauth-Scopes" in line:
            scope_str = line.split(":", 1)[-1].strip().lower()
            if "repo" in scope_str or "public_repo" in scope_str:
                scopes["can_fork"] = True
                scopes["can_create_prs"] = True
            break

    return scopes


def validate_repo(repo: str) -> bool:
    """Return True if the repository exists on GitHub."""
    result = _run_gh("repo", "view", repo, "--json", "name", check=False)
    return result.returncode == 0


def fetch_issue(repo: str, issue_number: int) -> Issue | None:
    """Fetch issue details from GitHub. Returns None on API failure."""
    result = _run_gh(
        "api",
        f"repos/{repo}/issues/{issue_number}",
        "--jq", '{"number": .number, "title": .title, "body": .body, "state": .state, "labels": [.labels[].name]}',
        check=False,
    )
    if result.returncode != 0:
        print(f"Error: Could not fetch issue #{issue_number} from {repo}.", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        return None
    data = json.loads(result.stdout)
    return Issue(
        number=data["number"],
        title=data["title"],
        body=data["body"] or "",
        labels=data["labels"],
        state=data["state"],
    )


def check_issue_active_work(repo: str, issue: Issue) -> tuple[bool, str]:
    """Check if an issue is already being actively worked on.

    Returns (is_active, reason). If is_active is True, the caller should
    skip this issue to avoid duplicating effort.
    """
    reasons: list[str] = []

    # 1. Check assignees
    result = _run_gh(
        "api",
        f"repos/{repo}/issues/{issue.number}",
        "--jq", "[.assignees[].login]",
        check=False,
    )
    if result.returncode == 0:
        assignees = json.loads(result.stdout)
        if assignees:
            reasons.append(f"assigned to: {', '.join(assignees)}")

    # 2. Check for work-in-progress labels
    wip_labels = {
        "in progress", "in-progress", "wip", "work in progress",
        "work-in-progress", "claimed", "assigned",
    }
    for label in issue.labels:
        if label.lower() in wip_labels:
            reasons.append(f"has label: '{label}'")

    # 3. Check for open/draft PRs that reference this issue
    result = _run_gh(
        "pr", "list",
        "--repo", repo,
        "--state", "open",
        "--search", f"#{issue.number}",
        "--json", "number,title,author,isDraft",
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        prs = json.loads(result.stdout)
        for pr in prs:
            kind = "Draft PR" if pr.get("isDraft") else "PR"
            author = pr.get("author", {}).get("login", "unknown")
            reasons.append(
                f"{kind} #{pr['number']} by {author}: {pr['title']}"
            )

    if reasons:
        return True, "Issue is already being worked on:\n  - " + "\n  - ".join(reasons)
    return False, ""


def search_issues(repo: str, limit: int = 30) -> list[Issue]:
    """Fetch a batch of open issues from the repo.

    Filters out pull requests (GitHub API returns PRs mixed in).
    Returns a list of Issue objects sorted by most recently updated.
    """
    result = _run_gh(
        "api",
        f"repos/{repo}/issues?state=open&per_page={limit}&sort=updated",
        check=False,
    )
    if result.returncode != 0:
        print(f"Error: Could not fetch issues from {repo}.", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        return []

    items = json.loads(result.stdout)
    issues: list[Issue] = []
    for item in items:
        # GitHub API returns PRs mixed in with issues — filter them out
        if "pull_request" in item:
            continue
        issues.append(Issue(
            number=item["number"],
            title=item["title"],
            body=item.get("body") or "",
            labels=[label["name"] for label in item.get("labels", [])],
            state=item["state"],
        ))
    return issues


def search_repos(description: str, limit: int = 10) -> list[Repository]:
    """Search GitHub for repositories matching a description.

    Strips meta-words (repo, project, etc.) that users add but aren't
    meaningful search terms. Filters for repos with >10 stars.
    Returns a list of Repository objects sorted by stars.
    """
    # Strip words that describe "a repo" rather than what the repo is about
    noise_words = {"repo", "repos", "repository", "repositories", "project", "projects", "library", "libraries"}
    words = re.split(r'[\s/]+', description)
    cleaned = " ".join(w for w in words if w.lower() not in noise_words)
    if not cleaned.strip():
        cleaned = description  # fallback to original if everything was stripped
    query = quote(f"{cleaned} stars:>10", safe="")
    result = _run_gh(
        "api",
        f"search/repositories?q={query}&sort=stars&order=desc&per_page={limit}",
        check=False,
    )
    if result.returncode != 0:
        print("Error: Could not search repositories.", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        return []

    data = json.loads(result.stdout)
    repos: list[Repository] = []
    for item in data.get("items", []):
        repos.append(Repository(
            full_name=item.get("full_name", ""),
            description=item.get("description") or "",
            language=item.get("language") or "",
            stars=item.get("stargazers_count", 0),
            open_issues_count=item.get("open_issues_count", 0),
            topics=item.get("topics") or [],
        ))
    return repos


def fork_repo(repo: str) -> str:
    """Fork the repo to the authenticated user's account. Returns fork 'owner/name'."""
    # --clone=false: don't clone locally, we clone separately
    result = _run_gh("repo", "fork", repo, "--clone=false", check=False)
    if result.returncode != 0:
        print(f"  Fork command failed (exit {result.returncode}):", file=sys.stderr)
        print(f"    {result.stderr.strip()}", file=sys.stderr)
        if "403" in result.stderr or "not accessible" in result.stderr:
            print("\n  Your token is missing fork/repo permissions.", file=sys.stderr)
            print("  For fine-grained PATs: enable 'Contents: Read and write' permission.", file=sys.stderr)
            print("  For classic tokens: enable the 'public_repo' scope.", file=sys.stderr)
            print("  Create a new token at: https://github.com/settings/tokens", file=sys.stderr)
            raise SystemExit(1)

    # Get the authenticated user's login to build the fork name.
    user_result = _run_gh("api", "user", "--jq", ".login")
    username = user_result.stdout.strip()
    repo_name = repo.split("/")[-1]
    fork_full = f"{username}/{repo_name}"

    # Wait for the fork to be available (GitHub can take a few seconds)
    for attempt in range(6):
        if validate_repo(fork_full):
            return fork_full
        print(f"  Waiting for fork to be available... (attempt {attempt + 1}/6)")
        time.sleep(5)

    print(f"Error: Fork {fork_full} not found after forking.", file=sys.stderr)
    raise SystemExit(1)


