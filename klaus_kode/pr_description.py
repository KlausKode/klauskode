"""PR description generation, show_changes, save_pr_description, and review."""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import TYPE_CHECKING
from urllib.parse import quote

from klaus_kode.claude_sdk import _quick_claude, run_claude_streaming
from klaus_kode.github import Issue
from klaus_kode.prompts import REVIEWER_SYSTEM_PROMPT, REVIEW_TOOLS
from klaus_kode.repo_ops import REPO_PATH

if TYPE_CHECKING:
    from klaus_kode.run_logger import RunLogger


def show_changes(default_branch: str) -> None:
    """Show the git diff of changes made."""
    print()
    print("[8/9] Showing changes made...")
    print("  Files changed:")
    subprocess.run(
        ["git", "--no-pager", "diff", "--stat", f"upstream/{default_branch}"],
        cwd=REPO_PATH,
    )
    print()
    subprocess.run(
        ["git", "--no-pager", "diff", f"upstream/{default_branch}"],
        cwd=REPO_PATH,
    )
    print()


def run_claude_review(
    default_branch: str,
    verbose: int = 0,
    logger: RunLogger | None = None,
    max_budget_usd: float | None = None,
    mcp_servers: dict | None = None,
    diff_output: str = "",
    start_time_global: float | None = None,
) -> None:
    """Run Claude self-review with streaming TUI. Exits with error if review is REJECTED."""
    from klaus_kode.prompts import build_review_prompt

    review_prompt = build_review_prompt(default_branch, diff_output=diff_output)

    output = run_claude_streaming(
        prompt=review_prompt,
        header="[8.5/9] Claude is self-reviewing...",
        activity="reviewing",
        verbose=verbose,
        max_turns=15,
        logger=logger,
        step_name="review",
        system_prompt=REVIEWER_SYSTEM_PROMPT,
        allowed_tools=REVIEW_TOOLS,
        max_budget_usd=max_budget_usd,
        mcp_servers=mcp_servers,
        start_time_global=start_time_global,
    )

    if output.strip() and output.strip().split('\n')[-1].strip().startswith("REJECTED"):
        print()
        print("=== SELF-REVIEW REJECTED ===")
        raise SystemExit(1)

    print()
    print("=== SELF-REVIEW APPROVED ===")


def generate_pr_description(issue: Issue, repo: str, default_branch: str,
                            diff_output: str = "") -> tuple[str, str]:
    """Ask Claude to generate a PR title and body for the changes made.

    Returns (title, body). Falls back to pr_template if Claude fails.
    """
    if not diff_output:
        # Capture diff if not provided
        result = subprocess.run(
            ["git", "--no-pager", "diff", f"upstream/{default_branch}"],
            capture_output=True, text=True, cwd=REPO_PATH,
        )
        diff_output = result.stdout[:20000]

    prompt = (
        f"Write a GitHub PR title and body for the changes shown below. "
        f"The PR addresses issue #{issue.number}: {issue.title}.\n\n"
        f"```diff\n{diff_output[:20000]}\n```\n\n"
        f"Return JSON with 'title' (string) and 'body' (markdown string)."
    )

    output_format = {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["title", "body"],
            "additionalProperties": False,
        },
    }

    try:
        raw = asyncio.run(_quick_claude(prompt, output_format=output_format))
        if raw.strip():
            data = json.loads(raw)
            title = data.get("title", "").strip()
            body = data.get("body", "").strip()
            if title:
                return title, body
    except Exception:
        pass

    # Fallback to static template
    from klaus_kode.pr_template import format_pr_body, format_pr_title
    return format_pr_title(issue), format_pr_body(issue, repo=repo)


def _build_compare_url(
    title: str,
    body: str,
    repo: str,
    head: str,
    default_branch: str,
    pr_file: str,
) -> tuple[str, str]:
    """Build a GitHub compare URL as fallback. Returns (url, body_note)."""
    params = {
        "quick_pull": "1",
        "title": title,
        "body": body,
    }
    query_str = "&".join(f"{k}={quote(v)}" for k, v in params.items())
    url = f"https://github.com/{repo}/compare/{default_branch}...{head}?{query_str}"

    if len(url) > 8000:
        params.pop("body")
        query_str = "&".join(f"{k}={quote(v)}" for k, v in params.items())
        url = f"https://github.com/{repo}/compare/{default_branch}...{head}?{query_str}"
        body_note = f"  (PR body too long for URL \u2014 paste from {pr_file})"
    else:
        body_note = ""

    return url, body_note


def save_pr_description(
    title: str,
    body: str,
    repo: str,
    fork: str,
    branch: str,
    default_branch: str,
) -> None:
    """Save PR description and print a clickable link to open a PR."""
    body += (
        "\n\n---\n"
        "This PR was automatically created by "
        "[Klaus Kode](https://github.com/nikste/klaus_kode) \u2014 "
        "an automated tool for solving open-source issues.\n\n"
        "Complaints, praise, or opt-out requests: klauskode@protonmail.com"
    )

    pr_file = "/workspace/pr_description.md"
    with open(pr_file, "w") as f:
        f.write(body)

    fork_owner = fork.split("/")[0]
    head = f"{fork_owner}:{branch}"

    url, body_note = _build_compare_url(title, body, repo, head, default_branch, pr_file)

    print("=" * 60)
    print("BRANCH PUSHED \u2014 click to open PR")
    print("=" * 60)
    print(f"Title : {title}")
    print()
    print(f"Open PR: {url}")
    if body_note:
        print(body_note)
    print(f"PR description also saved to: {pr_file}")
    print("=" * 60)
