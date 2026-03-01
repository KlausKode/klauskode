"""Claude SDK wrappers: quick one-shot helper and streaming TUI session."""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from typing import TYPE_CHECKING

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    query,
)

from klaus_kode.tui import (
    CYAN,
    DIM,
    GREEN,
    RESET,
    SPINNER_CHARS,
    STATUS_VERBS,
    YELLOW,
    format_tool_input,
    print_tool_result_output,
)
from klaus_kode.prompts import DISALLOWED_TOOLS, WORK_TOOLS

if TYPE_CHECKING:
    from klaus_kode.run_logger import RunLogger

# All repo operations run in this directory
REPO_PATH = "/workspace/repo"


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


def quick_claude_sync(
    prompt: str,
    model: str = "haiku",
    output_format: dict | None = None,
) -> str:
    """Synchronous wrapper around _quick_claude."""
    return asyncio.run(_quick_claude(prompt, model=model, output_format=output_format))


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
    start_time_global: float | None = None,
) -> str:
    """Run Claude via the Agent SDK with streaming TUI output.

    Returns the final text output from Claude.

    Args:
        start_time_global: Global pipeline start time for total elapsed display.
            Replaces the old _global_start module variable.
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
        if start_time_global is not None:
            return f"{int(time.time() - start_time_global)}s"
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
        line = f"  {CYAN}{ch} {verb}... ({_elapsed()} {activity} | total {_total_elapsed()}){RESET}"
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
                                _print_line(f"  {CYAN}{tl}{RESET}")
                            if logger:
                                logger.log_text_block(text)
                    elif isinstance(block, ToolUseBlock):
                        tool_name = block.name
                        tool_id = block.id
                        tool_input = block.input if hasattr(block, "input") else {}
                        summary = format_tool_input(tool_name, tool_input)
                        _print_line(
                            f"  {YELLOW}> {tool_name}{summary} ({_elapsed()}){RESET}"
                        )
                        num_tool_calls += 1
                        if logger:
                            logger.log_tool_call(tool_id, tool_name, tool_input)
                        _show_spinner(STATUS_VERBS[verb_idx])
                    elif isinstance(block, ToolResultBlock):
                        tool_id = block.tool_use_id if hasattr(block, "tool_use_id") else ""
                        is_error = block.is_error if hasattr(block, "is_error") else False
                        marker = f"{GREEN}\u2713" if not is_error else "\033[31m\u2717"
                        tool_name = "tool"
                        _print_line(f"  {marker} {tool_name} ({_elapsed()}){RESET}")

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
                        print_tool_result_output(output_text, verbose)

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
                    f"  {GREEN}\u2713 Done. Duration: {duration}, Total: {_total_elapsed()}{RESET}"
                )
                if final_output.strip() and verbose >= 1:
                    _print_line(f"  {DIM}--- Final output ---{RESET}")
                    for fl in final_output.strip().splitlines():
                        _print_line(f"  {fl}")

            elif isinstance(msg, SystemMessage):
                if verbose >= 2:
                    _print_line(f"  {DIM}[system] {msg.subtype}{RESET}")
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
    print(f"  \u2500\u2500 Summary \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    print(f"  Tool calls: {num_tool_calls} ({num_errors} errors)")
    print(f"  Duration: {step_duration}s")
    if num_errors > 0:
        print(f"  Errors:")
        for err in error_summaries:
            err_name = err.get("name", "?")
            err_text = err.get("error_text", "")
            print(f"    \u2717 {err_name} ({err_text})")
    print(f"  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    print()

    return final_output


def run_claude_streaming(
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
    start_time_global: float | None = None,
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
        start_time_global=start_time_global,
    ))
