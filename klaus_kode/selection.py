"""AI selection logic: pick_issue, pick_repo, suggest_branch_name, compliance checks."""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING

from klaus_kode.claude_sdk import _quick_claude
from klaus_kode.github import Issue, Repository

if TYPE_CHECKING:
    from klaus_kode.run_logger import RunLogger


def pick_issue(issues: list[Issue], description: str, logger: RunLogger | None = None) -> Issue:
    """Use Claude haiku to select the best issue matching a user description.

    Falls back to the first issue if parsing fails.
    """
    lines: list[str] = []
    for i, issue in enumerate(issues):
        labels = f" [{', '.join(issue.labels)}]" if issue.labels else ""
        body_preview = issue.body[:200].replace("\n", " ").strip()
        if body_preview:
            body_preview = f" \u2014 {body_preview}"
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
            f"{i}. {repo.full_name} ({repo.language}, {repo.stars}\u2605, "
            f"{repo.open_issues_count} open issues){topics} \u2014 {repo.description}"
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
