"""System prompts, prompt builders, and tool permission lists."""

from __future__ import annotations

from klaus_kode.github import Issue

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
- Do NOT push \u2014 that will happen automatically.
- Do NOT interact with GitHub (no `gh pr create`, `gh issue comment`, or any `gh` commands). Only write code.
- Follow the existing code style and conventions.
- If the project has a formatter or linter (e.g. `make style`, `black`, `ruff`), run it.
- Run any existing tests related to your changes to make sure nothing breaks.
- Make clean, focused commits with descriptive messages.

Efficiency (critical \u2014 follow these exactly):
- A repository context snapshot is provided in the prompt. Use it instead of exploring.
- NEVER re-read a file you already read in this session. You have the content in context.
- Plan your full approach BEFORE making any tool calls. State your plan in 3-5 bullet points, then execute.
- Batch ALL related edits to a single file in ONE Edit call, not multiple sequential edits.
- When reading a file, read the WHOLE file once. Do not read the same file in parts.
- Prefer Grep over Read when you need to find specific code patterns.
- Do NOT create todo lists or plans using TodoWrite \u2014 just execute directly.
- Target: complete the fix in under 30 total tool calls."""

REVIEWER_SYSTEM_PROMPT = """\
You are a focused code reviewer working inside a Docker container.

Environment:
- Working directory: /workspace/repo (the cloned repository)
- Python: use `python3` (not `python`). There is no `python` alias.

Rules:
- The complete diff is provided in the prompt. Focus ONLY on it.
- Do NOT re-read files already shown in the diff.
- Do NOT explore the repository \u2014 focus only on the changed files.
- If you need to fix something, do it with minimal tool calls.
- Only run tests if you made a fix. Do NOT re-run tests the worker already ran.
- After your review, output exactly one of:
  - APPROVED \u2014 if the changes are ready for a PR.
  - REJECTED: <reason> \u2014 if the changes have unfixable problems."""

# ---------------------------------------------------------------------------
# Allowed tools per step
# ---------------------------------------------------------------------------

WORK_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]
REVIEW_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]
PR_DESCRIPTION_TOOLS = ["Bash"]
DISALLOWED_TOOLS = ["TodoWrite", "Task", "WebSearch", "WebFetch"]


# ---------------------------------------------------------------------------
# Prompt builders (task content only \u2014 system prompt is separate)
# ---------------------------------------------------------------------------

def build_work_prompt(issue: Issue, repo: str, guidelines: str,
                      repo_context: str = "") -> str:
    """Build the task prompt for Claude to work on the issue."""
    prompt = f"Fix issue #{issue.number} in {repo}.\n\n"
    prompt += f"**Title:** {issue.title}\n**Body:**\n{issue.body}\n"
    if guidelines:
        prompt += f"\n**Contributing guidelines:**\n{guidelines}\n"
    if repo_context:
        prompt += f"\n**Repository context (pre-fetched \u2014 do NOT re-explore):**\n{repo_context}\n"
    return prompt


def build_review_prompt(default_branch: str, diff_output: str = "") -> str:
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
        "- APPROVED \u2014 if the changes are ready for a PR.\n"
        "- REJECTED: <reason> \u2014 if the changes have unfixable problems."
    )
    return prompt
