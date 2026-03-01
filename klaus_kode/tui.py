"""TUI helpers: spinners, colors, formatting for terminal output."""

from __future__ import annotations

import json

# ---------------------------------------------------------------------------
# Status verbs for spinner animation
# ---------------------------------------------------------------------------

STATUS_VERBS = [
    "Thinking", "Reasoning", "Analyzing", "Contemplating", "Processing",
    "Evaluating", "Investigating", "Exploring", "Synthesizing", "Reflecting",
    "Sauteing", "Catapulting", "Percolating", "Marinating", "Simmering",
    "Fermenting", "Distilling", "Crystallizing", "Composting", "Braising",
]

SPINNER_CHARS = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"

# ---------------------------------------------------------------------------
# ANSI color helpers
# ---------------------------------------------------------------------------

CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
DIM = "\033[2m"
RESET = "\033[0m"


def format_tool_input(tool_name: str, inp: dict) -> str:
    """Format tool input into a concise one-line summary."""
    if not inp:
        return ""
    if tool_name == "Read":
        return f" \u2192 {inp.get('file_path', '?')}"
    if tool_name == "Write":
        path = inp.get("file_path", "?")
        content = inp.get("content", "")
        return f" \u2192 {path} ({len(content)} chars)"
    if tool_name == "Edit":
        path = inp.get("file_path", "?")
        old = (inp.get("old_string", "") or "")[:60]
        return f" \u2192 {path} (replacing: {old!r}...)"
    if tool_name == "Bash":
        cmd = inp.get("command", "?")
        desc = inp.get("description", "")
        if desc:
            return f" \u2192 {desc}"
        return f" \u2192 {cmd[:200]}"
    if tool_name == "Glob":
        return f" \u2192 {inp.get('pattern', '?')}"
    if tool_name == "Grep":
        return f" \u2192 /{inp.get('pattern', '?')}/ in {inp.get('path', '.')}"
    if tool_name in ("Task", "WebSearch", "WebFetch"):
        return f" \u2192 {json.dumps(inp)[:150]}"
    # Generic: show first key-value
    for k, v in inp.items():
        return f" \u2192 {k}={str(v)[:100]}"
    return ""


def print_tool_result_output(output: str, verbose: int) -> None:
    """Print tool result output at the appropriate verbosity level."""
    if not output:
        return
    lines = output.strip().splitlines()
    if verbose >= 2:
        for ol in lines:
            print(f"    {DIM}{ol}{RESET}", flush=True)
    elif verbose >= 1:
        show = lines[:5]
        for ol in show:
            print(f"    {DIM}{ol[:200]}{RESET}", flush=True)
        if len(lines) > 5:
            print(f"    {DIM}... ({len(lines) - 5} more lines){RESET}", flush=True)
    else:
        show = lines[:3]
        for ol in show:
            print(f"    {DIM}{ol[:120]}{RESET}", flush=True)
        if len(lines) > 3:
            print(f"    {DIM}... ({len(lines) - 3} more lines){RESET}", flush=True)
