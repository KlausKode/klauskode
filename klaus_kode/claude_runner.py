"""Backwards-compatibility facade.

All logic has been extracted into dedicated modules:
- tui.py         : TUI helpers (spinners, colors, formatting)
- prompts.py     : System prompts, prompt builders, tool permission lists
- claude_sdk.py  : Claude SDK wrappers (quick queries, streaming sessions)
- repo_ops.py    : Git/filesystem operations (clone, branch, commit, push)
- selection.py   : AI selection logic (pick_issue, pick_repo, compliance)
- pr_description.py : PR description generation, review, save

This file re-exports everything so that existing imports continue to work.
It will be removed in a future version.
"""

from __future__ import annotations

# Re-export REPO_PATH constant
from klaus_kode.repo_ops import REPO_PATH  # noqa: F401

# Re-export prompts and tool lists
from klaus_kode.prompts import (  # noqa: F401
    WORKER_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
    WORK_TOOLS,
    REVIEW_TOOLS,
    PR_DESCRIPTION_TOOLS,
    DISALLOWED_TOOLS,
    build_work_prompt,
    build_review_prompt,
)

# Re-export TUI helpers
from klaus_kode.tui import (  # noqa: F401
    STATUS_VERBS,
    SPINNER_CHARS,
    format_tool_input,
    print_tool_result_output,
)

# Re-export Claude SDK wrappers
from klaus_kode.claude_sdk import (  # noqa: F401
    _quick_claude,
    run_claude_streaming,
)

# Re-export selection logic
from klaus_kode.selection import (  # noqa: F401
    pick_issue,
    pick_repo,
    suggest_branch_name,
    check_guidelines_compliance,
    parallel_pre_work,
)

# Re-export repo operations
from klaus_kode.repo_ops import (  # noqa: F401
    clone_repo,
    create_branch,
    read_contributing_guidelines,
    write_inner_claude_md,
    cleanup_inner_claude_md as _cleanup_inner_claude_md,
    gather_repo_context,
    commit_changes,
    push_branch,
)

# Re-export PR description logic
from klaus_kode.pr_description import (  # noqa: F401
    show_changes,
    run_claude_review,
    generate_pr_description,
    save_pr_description,
)

# Backwards-compat aliases for old private names
_build_work_prompt = build_work_prompt
_build_review_prompt = build_review_prompt
_format_tool_input = format_tool_input
_print_tool_result_output = print_tool_result_output

# Backwards compat: _global_start was previously set by cli.py
# Now replaced by start_time_global parameter on SDK functions.
_global_start: float | None = None


def run_claude_work(
    issue,
    repo: str,
    guidelines: str,
    verbose: int = 0,
    logger=None,
    max_budget_usd: float | None = None,
    mcp_servers: dict | None = None,
    repo_context: str = "",
) -> None:
    """Run Claude to work on the issue with streaming TUI output.

    Backwards-compat wrapper that forwards to the new modules.
    """
    prompt = build_work_prompt(issue, repo, guidelines, repo_context=repo_context)
    run_claude_streaming(
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
        start_time_global=_global_start,
    )
