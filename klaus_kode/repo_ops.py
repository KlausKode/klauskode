"""Git and filesystem operations: clone, branch, commit, push, repo context."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

from klaus_kode.github import Issue

if TYPE_CHECKING:
    from klaus_kode.run_logger import RunLogger

# All repo operations run in this directory
REPO_PATH = "/workspace/repo"


def clone_repo(repo: str, fork_repo: str, logger: RunLogger | None = None) -> str:
    """Configure git, clone the fork, set up upstream, detect default branch.

    Returns the default branch name (e.g. 'main' or 'master').
    """
    def _run(cmd, **kwargs):
        """Run a subprocess and optionally log it."""
        r = subprocess.run(cmd, **kwargs)
        if logger:
            stdout = ""
            stderr = ""
            if kwargs.get("capture_output"):
                stdout = r.stdout if isinstance(r.stdout, str) else (r.stdout or b"").decode("utf-8", errors="replace")
                stderr = r.stderr if isinstance(r.stderr, str) else (r.stderr or b"").decode("utf-8", errors="replace")
            logger.log_subprocess(cmd, r.returncode, stdout, stderr)
        return r

    print("[1/9] Configuring git...")
    _run(["git", "config", "--global", "user.name", "klaus-kode"], check=True)
    _run(
        ["git", "config", "--global", "user.email", "klaus-kode@users.noreply.github.com"],
        check=True,
    )
    _run(["git", "config", "--global", "core.pager", "cat"], check=True)

    print("[2/9] Setting up GitHub authentication...")
    _run(["gh", "auth", "setup-git"], check=True, capture_output=True)

    print(f"[3/9] Cloning fork {fork_repo} (shallow)...")
    _run(
        ["git", "clone", "--depth=1", "--single-branch",
         f"https://github.com/{fork_repo}.git", REPO_PATH],
        check=True,
    )

    # Set up upstream remote
    result = _run(
        ["git", "remote", "set-url", "upstream", f"https://github.com/{repo}.git"],
        capture_output=True,
        cwd=REPO_PATH,
    )
    if result.returncode != 0:
        _run(
            ["git", "remote", "add", "upstream", f"https://github.com/{repo}.git"],
            check=True,
            cwd=REPO_PATH,
        )
    _run(
        ["git", "fetch", "upstream", "main", "--depth=1"],
        capture_output=True,
        cwd=REPO_PATH,
    )
    # Also try fetching master in case that's the default
    _run(
        ["git", "fetch", "upstream", "master", "--depth=1"],
        capture_output=True,
        cwd=REPO_PATH,
    )

    # Detect default branch
    default_branch = "main"
    check_main = _run(
        ["git", "rev-parse", "--verify", "upstream/main"],
        capture_output=True,
        cwd=REPO_PATH,
    )
    if check_main.returncode != 0:
        check_master = _run(
            ["git", "rev-parse", "--verify", "upstream/master"],
            capture_output=True,
            cwd=REPO_PATH,
        )
        if check_master.returncode == 0:
            default_branch = "master"
        else:
            # Fallback: first remote branch from upstream
            result = _run(
                ["git", "branch", "-r"], capture_output=True, text=True, cwd=REPO_PATH,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("upstream/"):
                    default_branch = line.split("upstream/", 1)[1]
                    break

    print(f"  Default branch: {default_branch}")
    return default_branch


def create_branch(branch_name: str, default_branch: str) -> None:
    """Create and check out a new feature branch based on upstream default branch."""
    print(f"[5/9] Creating branch {branch_name}...")
    subprocess.run(
        ["git", "checkout", "-b", branch_name, f"upstream/{default_branch}"],
        check=True,
        cwd=REPO_PATH,
    )
    print(f"  Base branch: {default_branch}")


def read_contributing_guidelines() -> str:
    """Find and read contributing guideline files. Returns concatenated content or empty string."""
    print("[4/9] Reading contributing guidelines...")
    guideline_files = [
        "CONTRIBUTING.md", "CONTRIBUTING.rst", "CONTRIBUTING.txt",
        ".github/CONTRIBUTING.md", ".github/PULL_REQUEST_TEMPLATE.md",
    ]
    content = ""
    for f in guideline_files:
        path = os.path.join(REPO_PATH, f)
        if os.path.isfile(path):
            print(f"  Found: {f}")
            with open(path) as fh:
                # Read first 200 lines
                lines = []
                for i, line in enumerate(fh):
                    if i >= 200:
                        break
                    lines.append(line)
                content += f"\n--- {f} ---\n{''.join(lines)}\n"
    if not content:
        print("  No contributing guidelines found.")
    return content


def write_inner_claude_md(issue: Issue, repo: str, guidelines: str, branch_name: str) -> None:
    """Write a CLAUDE.md into the target repo for Claude to reference during work."""
    content = f"""\
# Project Context (written by klaus-kode)

## Current Task
Working on issue #{issue.number}: {issue.title}

## Environment
- Working directory: /workspace/repo
- Python: use `python3` (not `python`)
- Branch: {branch_name} (do NOT switch branches)
- Do NOT push \u2014 that happens automatically

## Contributing Guidelines
{guidelines if guidelines else "No contributing guidelines found."}
"""
    path = os.path.join(REPO_PATH, "CLAUDE.md")
    with open(path, "w") as f:
        f.write(content)

    # Exclude from git so it doesn't get committed
    exclude_path = os.path.join(REPO_PATH, ".git", "info", "exclude")
    try:
        with open(exclude_path, "a") as f:
            f.write("\nCLAUDE.md\n")
    except OSError:
        pass


def cleanup_inner_claude_md() -> None:
    """Remove the inner CLAUDE.md after work is done."""
    path = os.path.join(REPO_PATH, "CLAUDE.md")
    try:
        os.remove(path)
    except OSError:
        pass


def gather_repo_context() -> str:
    """Pre-gather repository context to reduce Claude's exploration overhead."""
    context_parts: list[str] = []

    # 1. Directory tree (top 2 levels, skip hidden/vendor dirs)
    result = subprocess.run(
        ["find", ".", "-maxdepth", "2", "-type", "f",
         "-not", "-path", "./.git/*",
         "-not", "-path", "./node_modules/*",
         "-not", "-path", "./.venv/*",
         "-not", "-path", "./vendor/*",
         "-not", "-path", "./__pycache__/*"],
        capture_output=True, text=True, cwd=REPO_PATH,
    )
    if result.returncode == 0:
        files = result.stdout.strip().splitlines()
        context_parts.append(f"## Repository file tree (top 2 levels, {len(files)} files):")
        context_parts.append("\n".join(files[:200]))

    # 2. README (first 100 lines)
    for readme in ["README.md", "README.rst", "README.txt", "README"]:
        path = os.path.join(REPO_PATH, readme)
        if os.path.isfile(path):
            with open(path) as f:
                lines = []
                for i, line in enumerate(f):
                    if i >= 100:
                        break
                    lines.append(line)
            context_parts.append(f"\n## {readme} (first 100 lines):\n{''.join(lines)}")
            break

    # 3. Package metadata (first 3KB)
    for meta_file in ["pyproject.toml", "package.json", "Cargo.toml", "go.mod",
                       "pom.xml", "setup.py", "setup.cfg"]:
        path = os.path.join(REPO_PATH, meta_file)
        if os.path.isfile(path):
            with open(path) as f:
                content = f.read(3000)
            context_parts.append(f"\n## {meta_file}:\n{content}")
            break

    return "\n".join(context_parts)


def _strip_coauthor_trailers(default_branch: str) -> None:
    """Rewrite commits on the feature branch to remove Co-Authored-By trailers.

    Claude Code automatically adds these to commits it creates, but we don't
    want to expose that in PRs to upstream repos.
    """
    subprocess.run(
        ["git", "filter-branch", "-f", "--msg-filter",
         r"sed '/^[Cc]o-[Aa]uthored-[Bb]y:/d'",
         f"upstream/{default_branch}..HEAD"],
        capture_output=True, cwd=REPO_PATH,
    )


def commit_changes(issue_number: int, default_branch: str, logger: RunLogger | None = None) -> bool:
    """Stage and commit all changes if Claude forgot to. Returns True if there were changes."""
    def _run(cmd, **kwargs):
        r = subprocess.run(cmd, **kwargs)
        if logger:
            stdout = ""
            stderr = ""
            if kwargs.get("capture_output"):
                stdout = r.stdout if isinstance(r.stdout, str) else (r.stdout or b"").decode("utf-8", errors="replace")
                stderr = r.stderr if isinstance(r.stderr, str) else (r.stderr or b"").decode("utf-8", errors="replace")
            logger.log_subprocess(cmd, r.returncode, stdout, stderr)
        return r

    # Check if there are uncommitted changes
    status = _run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=REPO_PATH,
    )
    if not status.stdout.strip():
        # Check if there are already commits beyond the base branch
        log = _run(
            ["git", "log", f"upstream/{default_branch}..HEAD", "--oneline"],
            capture_output=True, text=True, cwd=REPO_PATH,
        )
        if log.stdout.strip():
            # Strip co-author trailers from existing commits
            _strip_coauthor_trailers(default_branch)
            return True  # Claude committed properly
        print("  WARNING: No changes were made.")
        return False

    print("  Committing uncommitted changes...")
    _run(["git", "add", "-A"], check=True, cwd=REPO_PATH)
    _run(
        ["git", "commit", "-m", f"fix: address issue #{issue_number}"],
        check=True, cwd=REPO_PATH,
    )
    return True


def push_branch(branch: str, logger: RunLogger | None = None) -> None:
    """Push the branch to the fork."""
    print(f"[9/9] Pushing branch {branch} to fork...")
    cmd = ["git", "push", "--force", "origin", branch]
    # --force is intentional: supports retries if the branch was already pushed
    result = subprocess.run(cmd, check=True, cwd=REPO_PATH, capture_output=True)
    if logger:
        stdout = result.stdout if isinstance(result.stdout, str) else (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = result.stderr if isinstance(result.stderr, str) else (result.stderr or b"").decode("utf-8", errors="replace")
        logger.log_subprocess(cmd, result.returncode, stdout, stderr)
