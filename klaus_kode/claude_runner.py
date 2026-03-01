"""Run Claude inside the container to work on an issue and self-review.

Uses the claude-agent-sdk Python package for all Claude interactions.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import subprocess
import sys
import time
from typing import TYPE_CHECKING
from urllib.parse import quote

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    query,
)

if TYPE_CHECKING:
    from klaus_kode.run_logger import RunLogger

from klaus_kode.github import Issue, Repository

# All repo operations run in this directory
REPO_PATH = "/workspace/repo"

# Global start time — set by cli.main() so all steps can show total elapsed
_global_start: float | None = None

# ---------------------------------------------------------------------------
# System prompts (separated from task content)
# ---------------------------------------------------------------------------

WORKER_SYSTEM_PROMPT = """\
You are an expert open source contributor working inside a Docker container.

Environment:
- Working directory: /workspace/repo (the cloned repository)
- Python: use `python3` (not `python`). There is no `python` alias.
- Installing packages: use `python3 -m pip install --break-system-packages <pkg>` or create a venv first.

Rules:
- You are on a feature branch. Do NOT create or switch branches.
- Do NOT push — that will happen automatically.
- Do NOT interact with GitHub (no `gh pr create`, `gh issue comment`, or any `gh` commands). Only write code.
- Follow the existing code style and conventions.
- If the project has a formatter or linter (e.g. `make style`, `black`, `ruff`), run it.
- Run any existing tests related to your changes to make sure nothing breaks.
- Make clean, focused commits with descriptive messages.

Efficiency (critical — follow these exactly):
- A repository context snapshot is provided in the prompt. Use it instead of exploring.
- NEVER re-read a file you already read in this session. You have the content in context.
- Plan your full approach BEFORE making any tool calls. State your plan in 3-5 bullet points, then execute.
- Batch ALL related edits to a single file in ONE Edit call, not multiple sequential edits.
- When reading a file, read the WHOLE file once. Do not read the same file in parts.
- Prefer Grep over Read when you need to find specific code patterns.
- Do NOT create todo lists or plans using TodoWrite — just execute directly.
- Target: complete the fix in under 30 total tool calls."""

REVIEWER_SYSTEM_PROMPT = """\
You are a focused code reviewer working inside a Docker container.

Environment:
- Working directory: /workspace/repo (the cloned repository)
- Python: use `python3` (not `python`). There is no `python` alias.

Rules:
- The complete diff is provided in the prompt. Focus ONLY on it.
- Do NOT re-read files already shown in the diff.
- Do NOT explore the repository — focus only on the changed files.
- If you need to fix something, do it with minimal tool calls.
- Only run tests if you made a fix. Do NOT re-run tests the worker already ran.
- After your review, output exactly one of:
  - APPROVED — if the changes are ready for a PR.
  - REJECTED: <reason> — if the changes have unfixable problems."""

# ---------------------------------------------------------------------------
# Allowed tools per step
# ---------------------------------------------------------------------------

WORK_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]
REVIEW_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]
PR_DESCRIPTION_TOOLS = ["Bash"]
DISALLOWED_TOOLS = ["TodoWrite", "Task", "WebSearch", "WebFetch"]

# ---------------------------------------------------------------------------
# TUI helpers (spinners, colors, formatting)
# ---------------------------------------------------------------------------

STATUS_VERBS = [
    "Thinking", "Reasoning", "Analyzing", "Contemplating", "Processing",
    "Evaluating", "Investigating", "Exploring", "Synthesizing", "Reflecting",
    "Sauteing", "Catapulting", "Percolating", "Marinating", "Simmering",
    "Fermenting", "Distilling", "Crystallizing", "Composting", "Braising",
]

SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# ANSI color helpers
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _format_tool_input(tool_name: str, inp: dict) -> str:
    """Format tool input into a concise one-line summary."""
    if not inp:
        return ""
    if tool_name == "Read":
        return f" → {inp.get('file_path', '?')}"
    if tool_name == "Write":
        path = inp.get("file_path", "?")
        content = inp.get("content", "")
        return f" → {path} ({len(content)} chars)"
    if tool_name == "Edit":
        path = inp.get("file_path", "?")
        old = (inp.get("old_string", "") or "")[:60]
        return f" → {path} (replacing: {old!r}...)"
    if tool_name == "Bash":
        cmd = inp.get("command", "?")
        desc = inp.get("description", "")
        if desc:
            return f" → {desc}"
        return f" → {cmd[:200]}"
    if tool_name == "Glob":
        return f" → {inp.get('pattern', '?')}"
    if tool_name == "Grep":
        return f" → /{inp.get('pattern', '?')}/ in {inp.get('path', '.')}"
    if tool_name in ("Task", "WebSearch", "WebFetch"):
        return f" → {json.dumps(inp)[:150]}"
    # Generic: show first key-value
    for k, v in inp.items():
        return f" → {k}={str(v)[:100]}"
    return ""


def _print_tool_result_output(output: str, verbose: int) -> None:
    """Print tool result output at the appropriate verbosity level."""
    if not output:
        return
    lines = output.strip().splitlines()
    if verbose >= 2:
        for ol in lines:
            print(f"    {_DIM}{ol}{_RESET}", flush=True)
    elif verbose >= 1:
        show = lines[:5]
        for ol in show:
            print(f"    {_DIM}{ol[:200]}{_RESET}", flush=True)
        if len(lines) > 5:
            print(f"    {_DIM}... ({len(lines) - 5} more lines){_RESET}", flush=True)
    else:
        show = lines[:3]
        for ol in show:
            print(f"    {_DIM}{ol[:120]}{_RESET}", flush=True)
        if len(lines) > 3:
            print(f"    {_DIM}... ({len(lines) - 3} more lines){_RESET}", flush=True)


# ---------------------------------------------------------------------------
# Quick one-shot Claude helper (for utility functions)
# ---------------------------------------------------------------------------

async def _quick_claude(
    prompt: str,
    model: str = "haiku",
    output_format: dict | None = None,
) -> str:
    """Run a quick one-shot Claude query with no tools. Returns text output."""
    options = ClaudeAgentOptions(
        model=model,
        permission_mode="bypassPermissions",
        allowed_tools=[],
        max_turns=1,
    )
    if output_format:
        options.output_format = output_format

    result_text = ""
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, ResultMessage):
            if msg.structured_output is not None:
                # output_format was used — result is in structured_output
                if isinstance(msg.structured_output, str):
                    result_text = msg.structured_output
                else:
                    result_text = json.dumps(msg.structured_output)
            elif msg.result:
                result_text = msg.result
        elif isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    result_text += block.text
    return result_text


# ---------------------------------------------------------------------------
# Utility functions (pick_issue, pick_repo, suggest_branch_name, etc.)
# ---------------------------------------------------------------------------

def pick_issue(issues: list[Issue], description: str, logger: RunLogger | None = None) -> Issue:
    """Use Claude haiku to select the best issue matching a user description.

    Falls back to the first issue if parsing fails.
    """
    lines: list[str] = []
    for i, issue in enumerate(issues):
        labels = f" [{', '.join(issue.labels)}]" if issue.labels else ""
        body_preview = issue.body[:200].replace("\n", " ").strip()
        if body_preview:
            body_preview = f" — {body_preview}"
        lines.append(f"{issue.number}. {issue.title}{labels}{body_preview}")

    issue_list = "\n".join(lines)
    prompt = (
        f"Given these open GitHub issues:\n\n{issue_list}\n\n"
        f"Pick the ONE issue that best matches this user request: '{description}'.\n"
        f"The request might be a difficulty level (easy/medium/hard), a topic description, "
        f"or a specific technical detail.\n"
        f"Consider: how well it matches the request, whether it's self-contained, "
        f"and whether it has a clear fix.\n"
        f"Return JSON with a single key 'issue_number' set to the issue number."
    )

    output_format = {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {"issue_number": {"type": "integer"}},
            "required": ["issue_number"],
            "additionalProperties": False,
        },
    }

    try:
        raw = asyncio.run(_quick_claude(prompt, output_format=output_format))
        if logger:
            logger.log_subprocess(
                ["claude-agent-sdk", "pick_issue"], 0, raw, "",
            )
        data = json.loads(raw)
        chosen_number = data.get("issue_number")
        if chosen_number is not None:
            for issue in issues:
                if issue.number == chosen_number:
                    return issue
    except Exception:
        pass

    # Fallback to first issue
    return issues[0]


def pick_repo(repos: list[Repository], description: str, logger: RunLogger | None = None) -> Repository:
    """Use Claude haiku to select the best repository matching a user description.

    Falls back to the first repo if parsing fails.
    """
    lines: list[str] = []
    for i, repo in enumerate(repos, 1):
        topics = f" [{', '.join(repo.topics)}]" if repo.topics else ""
        lines.append(
            f"{i}. {repo.full_name} ({repo.language}, {repo.stars}★, "
            f"{repo.open_issues_count} open issues){topics} — {repo.description}"
        )

    repo_list = "\n".join(lines)
    prompt = (
        f"Given these GitHub repositories:\n\n{repo_list}\n\n"
        f"Pick the ONE repository that best matches this user request: '{description}'.\n"
        f"Consider: relevance to the request, number of open issues, "
        f"whether it's beginner-friendly, and project activity.\n"
        f"Return JSON with a single key 'repo_index' set to the 1-based index."
    )

    output_format = {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {"repo_index": {"type": "integer"}},
            "required": ["repo_index"],
            "additionalProperties": False,
        },
    }

    try:
        raw = asyncio.run(_quick_claude(prompt, output_format=output_format))
        if logger:
            logger.log_subprocess(
                ["claude-agent-sdk", "pick_repo"], 0, raw, "",
            )
        data = json.loads(raw)
        idx = data.get("repo_index", 1) - 1  # 1-indexed to 0-indexed
        if 0 <= idx < len(repos):
            return repos[idx]
    except Exception:
        pass

    # Fallback to first repo
    return repos[0]


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


def suggest_branch_name(issue: Issue, guidelines: str) -> str:
    """Ask Claude to suggest a branch name following the project's conventions.

    Falls back to fix/issue-{N} if guidelines are absent or Claude's output is invalid.
    """
    fallback = f"fix/issue-{issue.number}"

    if not guidelines:
        return fallback

    prompt = (
        f"Given these contributing guidelines, what branch name should I use for "
        f"issue #{issue.number} titled '{issue.title}'? "
        f"Return JSON with a single key 'branch_name'.\n\n{guidelines}"
    )

    output_format = {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {"branch_name": {"type": "string"}},
            "required": ["branch_name"],
            "additionalProperties": False,
        },
    }

    try:
        raw = asyncio.run(_quick_claude(prompt, output_format=output_format))
        data = json.loads(raw)
        branch = data.get("branch_name", "").strip()
        # Validate: only valid git branch name characters, reasonable length
        if branch and re.match(r'^[\w\-./]+$', branch) and len(branch) <= 100:
            return branch
    except Exception:
        pass

    return fallback


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


def check_guidelines_compliance(guidelines: str) -> bool:
    """Use Claude to check if we can comply with contributing guidelines.

    Returns True if we should proceed, False if we should abort.
    """
    print()
    print("============================================")
    print("[6/9] Checking if we can comply with contributing guidelines...")
    print("============================================")

    if not guidelines:
        print("  No contributing guidelines found, proceeding.")
        return True

    prompt = f"""\
You are an automated tool (klaus-kode) that works on GitHub issues by:
- Cloning a repo in a Docker container
- Making code changes on a branch
- Running available linters/formatters
- Running existing tests
- Submitting a PR from a fork

Here are the contributing guidelines for this project:
{guidelines}

Can this automated workflow comply with these guidelines? Check for:
- Do they require a CLA signature we cannot provide?
- Do they require discussion/approval BEFORE submitting a PR?
- Do they explicitly ban automated/bot PRs?
- Do they require steps we cannot perform (e.g. manual QA, specific hardware)?

Return JSON with 'decision' (either "PROCEED" or "ABORT") and 'reason' (short explanation)."""

    output_format = {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["PROCEED", "ABORT"]},
                "reason": {"type": "string"},
            },
            "required": ["decision", "reason"],
            "additionalProperties": False,
        },
    }

    try:
        raw = asyncio.run(_quick_claude(prompt, output_format=output_format))
        data = json.loads(raw)
        decision = data.get("decision", "PROCEED")
        reason = data.get("reason", "")
        print(f"  Decision: {decision}")
        if reason:
            print(f"  Reason: {reason}")

        if decision == "ABORT":
            print()
            print("=== CANNOT COMPLY WITH CONTRIBUTING GUIDELINES ===")
            return False
    except Exception as e:
        print(f"  Warning: Guidelines check failed ({e}), proceeding anyway.")

    print("  Guidelines check passed.")
    return True


def parallel_pre_work(issue: Issue, guidelines: str) -> tuple[str, bool]:
    """Run branch name suggestion and guidelines check in parallel.

    Returns (branch_name, should_proceed).
    """
    if not guidelines:
        return f"fix/issue-{issue.number}", True

    branch_prompt = (
        f"Given these contributing guidelines, what branch name should I use for "
        f"issue #{issue.number} titled '{issue.title}'? "
        f"Return JSON with a single key 'branch_name'.\n\n{guidelines}"
    )
    branch_schema = {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {"branch_name": {"type": "string"}},
            "required": ["branch_name"],
            "additionalProperties": False,
        },
    }

    compliance_prompt = f"""\
You are an automated tool (klaus-kode) that works on GitHub issues by:
- Cloning a repo in a Docker container
- Making code changes on a branch
- Running available linters/formatters
- Running existing tests
- Submitting a PR from a fork

Here are the contributing guidelines for this project:
{guidelines}

Can this automated workflow comply with these guidelines? Check for:
- Do they require a CLA signature we cannot provide?
- Do they require discussion/approval BEFORE submitting a PR?
- Do they explicitly ban automated/bot PRs?
- Do they require steps we cannot perform (e.g. manual QA, specific hardware)?

Return JSON with 'decision' (either "PROCEED" or "ABORT") and 'reason' (short explanation)."""

    compliance_schema = {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["PROCEED", "ABORT"]},
                "reason": {"type": "string"},
            },
            "required": ["decision", "reason"],
            "additionalProperties": False,
        },
    }

    async def _run_parallel():
        return await asyncio.gather(
            _quick_claude(branch_prompt, output_format=branch_schema),
            _quick_claude(compliance_prompt, output_format=compliance_schema),
        )

    fallback_branch = f"fix/issue-{issue.number}"

    try:
        branch_raw, compliance_raw = asyncio.run(_run_parallel())
    except Exception as e:
        print(f"  Warning: Parallel pre-work failed ({e}), using defaults.")
        return fallback_branch, True

    # Parse branch name
    branch_name = fallback_branch
    try:
        data = json.loads(branch_raw)
        b = data.get("branch_name", "").strip()
        if b and re.match(r'^[\w\-./]+$', b) and len(b) <= 100:
            branch_name = b
    except Exception:
        pass

    # Parse compliance
    should_proceed = True
    try:
        data = json.loads(compliance_raw)
        decision = data.get("decision", "PROCEED")
        reason = data.get("reason", "")
        print(f"  Guidelines decision: {decision}")
        if reason:
            print(f"  Reason: {reason}")
        if decision == "ABORT":
            print()
            print("=== CANNOT COMPLY WITH CONTRIBUTING GUIDELINES ===")
            should_proceed = False
    except Exception as e:
        print(f"  Warning: Guidelines check failed ({e}), proceeding anyway.")

    return branch_name, should_proceed


# ---------------------------------------------------------------------------
# Inner CLAUDE.md for target repos
# ---------------------------------------------------------------------------

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
- Do NOT push — that happens automatically

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


def _cleanup_inner_claude_md() -> None:
    """Remove the inner CLAUDE.md after work is done."""
    path = os.path.join(REPO_PATH, "CLAUDE.md")
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Pre-fetch repo context (reduces Claude's exploration overhead)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Build prompts (task content only — system prompt is separate)
# ---------------------------------------------------------------------------

def _build_work_prompt(issue: Issue, repo: str, guidelines: str,
                       repo_context: str = "") -> str:
    """Build the task prompt for Claude to work on the issue."""
    prompt = f"Fix issue #{issue.number} in {repo}.\n\n"
    prompt += f"**Title:** {issue.title}\n**Body:**\n{issue.body}\n"
    if guidelines:
        prompt += f"\n**Contributing guidelines:**\n{guidelines}\n"
    if repo_context:
        prompt += f"\n**Repository context (pre-fetched — do NOT re-explore):**\n{repo_context}\n"
    return prompt


def _build_review_prompt(default_branch: str, diff_output: str = "") -> str:
    """Build the task prompt for Claude to self-review changes."""
    prompt = "Review the changes made to fix the issue.\n\n"

    if diff_output:
        prompt += f"Here is the complete diff:\n```\n{diff_output}\n```\n\n"
    else:
        prompt += (
            f"Run `git diff upstream/{default_branch}` to see the changes "
            f"against the base branch.\n\n"
        )

    prompt += (
        "Check for:\n"
        "- Correctness: Does the implementation actually address the issue?\n"
        "- Test coverage: Are there tests for the new behavior?\n"
        "- Code style: Is it consistent with the rest of the codebase?\n"
        "- Security: No secrets, no injection vulnerabilities.\n"
        "- Scope: No unrelated changes or unnecessary refactoring.\n\n"
        "If you find issues, fix them now using minimal tool calls.\n"
        "Do NOT re-read files that are shown in the diff.\n\n"
        "After your review, output exactly one of:\n"
        "- APPROVED — if the changes are ready for a PR.\n"
        "- REJECTED: <reason> — if the changes have unfixable problems."
    )
    return prompt


# ---------------------------------------------------------------------------
# Streaming Claude session with TUI
# ---------------------------------------------------------------------------

async def _run_claude_streaming_async(
    prompt: str,
    header: str,
    activity: str,
    verbose: int = 0,
    max_turns: int = 50,
    logger: RunLogger | None = None,
    step_name: str = "",
    system_prompt: str = "",
    allowed_tools: list[str] | None = None,
    max_budget_usd: float | None = None,
    mcp_servers: dict | None = None,
) -> str:
    """Run Claude via the Agent SDK with streaming TUI output.

    Returns the final text output from Claude.
    """
    if logger:
        logger.log_step_start(step_name or activity, prompt=prompt, max_turns=max_turns)

    print()
    print("============================================")
    print(header)
    print("============================================")
    print()

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        cwd=REPO_PATH,
        allowed_tools=allowed_tools or WORK_TOOLS,
        disallowed_tools=DISALLOWED_TOOLS,
    )

    if system_prompt:
        options.system_prompt = system_prompt
    if max_budget_usd is not None:
        options.max_budget_usd = max_budget_usd
    if mcp_servers:
        options.mcp_servers = mcp_servers

    start_time = time.time()
    spinner_idx = 0
    verb_idx = random.randint(0, len(STATUS_VERBS) - 1)
    last_verb_change = time.time()
    last_spinner_line = ""
    final_output = ""

    # Tracking for terminal summary
    num_tool_calls = 0
    num_errors = 0
    error_summaries: list[dict] = []

    def _elapsed() -> str:
        return f"{int(time.time() - start_time)}s"

    def _total_elapsed() -> str:
        if _global_start is not None:
            return f"{int(time.time() - _global_start)}s"
        return _elapsed()

    def _clear_spinner():
        nonlocal last_spinner_line
        if last_spinner_line:
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()
            last_spinner_line = ""

    def _show_spinner(verb: str):
        nonlocal spinner_idx, last_spinner_line
        ch = SPINNER_CHARS[spinner_idx % len(SPINNER_CHARS)]
        spinner_idx += 1
        line = f"  {_CYAN}{ch} {verb}... ({_elapsed()} {activity} | total {_total_elapsed()}){_RESET}"
        sys.stdout.write(f"\r\033[2K{line}")
        sys.stdout.flush()
        last_spinner_line = line

    def _print_line(msg: str):
        _clear_spinner()
        print(msg, flush=True)

    _show_spinner(STATUS_VERBS[verb_idx])

    try:
        async for msg in query(prompt=prompt, options=options):
            # Rotate verb periodically
            if time.time() - last_verb_change > 5:
                verb_idx = (verb_idx + 1) % len(STATUS_VERBS)
                last_verb_change = time.time()

            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text = block.text.strip()
                        if text:
                            for tl in text.splitlines():
                                _print_line(f"  {_CYAN}{tl}{_RESET}")
                            if logger:
                                logger.log_text_block(text)
                    elif isinstance(block, ToolUseBlock):
                        tool_name = block.name
                        tool_id = block.id
                        tool_input = block.input if hasattr(block, "input") else {}
                        summary = _format_tool_input(tool_name, tool_input)
                        _print_line(
                            f"  {_YELLOW}> {tool_name}{summary} ({_elapsed()}){_RESET}"
                        )
                        num_tool_calls += 1
                        if logger:
                            logger.log_tool_call(tool_id, tool_name, tool_input)
                        _show_spinner(STATUS_VERBS[verb_idx])
                    elif isinstance(block, ToolResultBlock):
                        tool_id = block.tool_use_id if hasattr(block, "tool_use_id") else ""
                        is_error = block.is_error if hasattr(block, "is_error") else False
                        marker = f"{_GREEN}✓" if not is_error else "\033[31m✗"
                        tool_name = "tool"
                        _print_line(f"  {marker} {tool_name} ({_elapsed()}){_RESET}")

                        output_text = ""
                        if hasattr(block, "content"):
                            if isinstance(block.content, str):
                                output_text = block.content
                            elif isinstance(block.content, list):
                                parts = []
                                for b in block.content:
                                    if hasattr(b, "text"):
                                        parts.append(b.text)
                                output_text = "\n".join(parts)
                        _print_tool_result_output(output_text, verbose)

                        if logger:
                            logger.log_tool_result(tool_id, tool_name, output_text, is_error)
                        if is_error:
                            num_errors += 1
                            error_summaries.append({
                                "name": tool_name,
                                "error_text": output_text.strip().split("\n")[0][:200] if output_text else "",
                            })
                        _show_spinner(STATUS_VERBS[verb_idx])
                    else:
                        # Other block types (thinking, etc.) — just show spinner
                        _show_spinner(STATUS_VERBS[verb_idx])

            elif isinstance(msg, ResultMessage):
                _clear_spinner()
                final_output = msg.result if isinstance(msg.result, str) else str(msg.result)
                duration = _elapsed()
                _print_line(
                    f"  {_GREEN}✓ Done. Duration: {duration}, Total: {_total_elapsed()}{_RESET}"
                )
                if final_output.strip() and verbose >= 1:
                    _print_line(f"  {_DIM}--- Final output ---{_RESET}")
                    for fl in final_output.strip().splitlines():
                        _print_line(f"  {fl}")

            elif isinstance(msg, SystemMessage):
                if verbose >= 2:
                    _print_line(f"  {_DIM}[system] {msg.subtype}{_RESET}")
                _show_spinner(STATUS_VERBS[verb_idx])

            else:
                _show_spinner(STATUS_VERBS[verb_idx])

    except KeyboardInterrupt:
        _clear_spinner()
        print("\n  [interrupted]", flush=True)
    finally:
        _clear_spinner()

    step_duration = round(time.time() - start_time, 1)

    # Log to RunLogger
    if logger:
        logger.log_claude_result(
            output=final_output,
            exit_code=0,
        )
        logger.log_step_end(step_name or activity, exit_code=0)

    # Print end-of-run summary
    _clear_spinner()
    print()
    print(f"  ── Summary ──────────────────────────")
    print(f"  Tool calls: {num_tool_calls} ({num_errors} errors)")
    print(f"  Duration: {step_duration}s")
    if num_errors > 0:
        print(f"  Errors:")
        for err in error_summaries:
            err_name = err.get("name", "?")
            err_text = err.get("error_text", "")
            print(f"    ✗ {err_name} ({err_text})")
    print(f"  ────────────────────────────────────")
    print()

    return final_output


def _run_claude_streaming(
    prompt: str,
    header: str,
    activity: str,
    verbose: int = 0,
    max_turns: int = 50,
    logger: RunLogger | None = None,
    step_name: str = "",
    system_prompt: str = "",
    allowed_tools: list[str] | None = None,
    max_budget_usd: float | None = None,
    mcp_servers: dict | None = None,
) -> str:
    """Synchronous wrapper around _run_claude_streaming_async."""
    return asyncio.run(_run_claude_streaming_async(
        prompt=prompt,
        header=header,
        activity=activity,
        verbose=verbose,
        max_turns=max_turns,
        logger=logger,
        step_name=step_name,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
        max_budget_usd=max_budget_usd,
        mcp_servers=mcp_servers,
    ))


# ---------------------------------------------------------------------------
# Public entry points (stay synchronous for cli.py)
# ---------------------------------------------------------------------------

def run_claude_work(
    issue: Issue,
    repo: str,
    guidelines: str,
    verbose: int = 0,
    logger: RunLogger | None = None,
    max_budget_usd: float | None = None,
    mcp_servers: dict | None = None,
    repo_context: str = "",
) -> None:
    """Run Claude to work on the issue with streaming TUI output."""
    prompt = _build_work_prompt(issue, repo, guidelines, repo_context=repo_context)
    _run_claude_streaming(
        prompt=prompt,
        header="[7/9] Claude is working on the issue...",
        activity="implementing",
        verbose=verbose,
        max_turns=25,
        logger=logger,
        step_name="work",
        system_prompt=WORKER_SYSTEM_PROMPT,
        allowed_tools=WORK_TOOLS,
        max_budget_usd=max_budget_usd,
        mcp_servers=mcp_servers,
    )


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
) -> None:
    """Run Claude self-review with streaming TUI. Exits with error if review is REJECTED."""
    review_prompt = _build_review_prompt(default_branch, diff_output=diff_output)

    output = _run_claude_streaming(
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
        body_note = f"  (PR body too long for URL — paste from {pr_file})"
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
    """Push branch to fork and print a clickable link to open a PR."""
    body += (
        "\n\n---\n"
        "This PR was automatically created by "
        "[Klaus Kode](https://github.com/nikste/klaus_kode) — "
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
    print("BRANCH PUSHED — click to open PR")
    print("=" * 60)
    print(f"Title : {title}")
    print()
    print(f"Open PR: {url}")
    if body_note:
        print(body_note)
    print(f"PR description also saved to: {pr_file}")
    print("=" * 60)


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
