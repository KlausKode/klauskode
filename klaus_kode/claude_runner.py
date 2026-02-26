"""Run Claude inside the container to work on an issue and self-review.

All functions use subprocess directly — no bash script generation.
"""

from __future__ import annotations

import json
import os
import random
import re
import select
import subprocess
from urllib.parse import quote
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from klaus_kode.run_logger import RunLogger

from klaus_kode.github import Issue, Repository

# All repo operations run in this directory
REPO_PATH = "/workspace/repo"

# Global start time — set by cli.main() so all steps can show total elapsed
_global_start: float | None = None


def pick_issue(issues: list[Issue], description: str, logger: RunLogger | None = None) -> Issue:
    """Use Claude haiku to select the best issue matching a user description.

    Falls back to the first issue if parsing fails.
    """
    # Build a numbered list of issues for Claude to choose from
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
        f"Reply with ONLY the issue number, nothing else."
    )

    cmd = ["claude", "-p", "--dangerously-skip-permissions", "--model", "haiku"]
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True)

    if logger:
        logger.log_subprocess(cmd, result.returncode, result.stdout, result.stderr)

    if result.returncode == 0 and result.stdout.strip():
        # Extract the issue number from Claude's response
        match = re.search(r'\d+', result.stdout.strip())
        if match:
            chosen_number = int(match.group())
            for issue in issues:
                if issue.number == chosen_number:
                    return issue

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
        f"Reply with ONLY the number (1-{len(repos)}), nothing else."
    )

    cmd = ["claude", "-p", "--dangerously-skip-permissions", "--model", "haiku"]
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True)

    if logger:
        logger.log_subprocess(cmd, result.returncode, result.stdout, result.stderr)

    if result.returncode == 0 and result.stdout.strip():
        match = re.search(r'\d+', result.stdout.strip())
        if match:
            idx = int(match.group()) - 1  # 1-indexed to 0-indexed
            if 0 <= idx < len(repos):
                return repos[idx]

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
        f"Reply with ONLY the branch name, nothing else.\n\n{guidelines}"
    )

    result = subprocess.run(
        ["claude", "-p", "--dangerously-skip-permissions", "--model", "haiku"],
        input=prompt,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return fallback

    branch = result.stdout.strip()

    # Validate: only valid git branch name characters, reasonable length
    if not branch or not re.match(r'^[\w\-./]+$', branch) or len(branch) > 100:
        return fallback

    return branch


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
    """Run claude -p to check if we can comply with contributing guidelines.

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

Output exactly one of:
- PROCEED — if the automated workflow can reasonably comply
- ABORT: <reason> — if there is a hard blocker"""

    result = subprocess.run(
        ["claude", "-p", "--dangerously-skip-permissions", "--model", "haiku"],
        input=prompt,
        capture_output=True,
        text=True,
    )
    output = result.stdout.strip()
    print(output)

    if output.strip().split('\n')[-1].strip().startswith("ABORT"):
        print()
        print("=== CANNOT COMPLY WITH CONTRIBUTING GUIDELINES ===")
        return False

    print("  Guidelines check passed.")
    return True


def _build_work_prompt(issue: Issue, repo: str, guidelines: str) -> str:
    """Build the prompt for Claude to work on the issue."""
    prompt = f"""\
You are working on issue #{issue.number} in the repository {repo}.

**Issue title:** {issue.title}

**Issue body:**
{issue.body}

**Environment:**
- Working directory: /workspace/repo (the cloned repository)
- Python: use `python3` (not `python`). There is no `python` alias.
- Installing packages: use `python3 -m pip install --break-system-packages <pkg>` or create a venv first.
- Start by running `ls` to understand the project structure before making changes.

Instructions:
1. Read and understand the issue thoroughly.
2. Check the CONTRIBUTING.md or similar docs to understand the project's contribution guidelines.
3. You are already on a feature branch. Do NOT create or switch branches.
4. Implement the fix or feature described in the issue.
5. Follow the existing code style and conventions.
6. If the project has a formatter or linter (e.g. `make style`, `black`, `ruff`), run it.
7. Run any existing tests related to your changes to make sure nothing breaks.
8. Make clean, focused commits with descriptive messages.
9. Do NOT push — that will happen automatically in a later step."""

    if guidelines:
        prompt += f"""

Here are the contributing guidelines for this project:
{guidelines}

Follow these guidelines carefully."""

    return prompt


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


def _print_tool_result(d: dict, verbose: int) -> None:
    """Print tool result output at the appropriate verbosity level."""
    output = d.get("output", "") or d.get("content", "")
    if isinstance(output, list):
        parts = []
        for block in output:
            if isinstance(block, dict) and block.get("text"):
                parts.append(block["text"])
        output = "\n".join(parts)
    if not output:
        return
    lines = output.strip().splitlines()
    if verbose >= 2:
        for ol in lines:
            print(f"    {_DIM}{ol}{_RESET}", flush=True)
    elif verbose >= 1:
        # Show up to 5 lines, truncated
        show = lines[:5]
        for ol in show:
            print(f"    {_DIM}{ol[:200]}{_RESET}", flush=True)
        if len(lines) > 5:
            print(f"    {_DIM}... ({len(lines) - 5} more lines){_RESET}", flush=True)
    else:
        # Default: show first 3 lines, heavily truncated
        show = lines[:3]
        for ol in show:
            print(f"    {_DIM}{ol[:120]}{_RESET}", flush=True)
        if len(lines) > 3:
            print(f"    {_DIM}... ({len(lines) - 3} more lines){_RESET}", flush=True)


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


def _iter_stdout_lines(process: subprocess.Popen):
    """Iterate over process stdout, yielding (line_str | None).

    Yields complete JSON lines when available, or None every ~1 second
    as a heartbeat for spinner animation.
    """
    fd = process.stdout.fileno()
    buf = ""
    while True:
        ready, _, _ = select.select([fd], [], [], 1.0)
        if ready:
            chunk = os.read(fd, 65536)
            if not chunk:
                if buf:
                    yield buf
                break
            buf += chunk.decode("utf-8", errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                yield line
        else:
            if process.poll() is not None:
                if buf:
                    yield buf
                break
            yield None  # heartbeat


def _run_claude_streaming(
    prompt: str, header: str, activity: str, verbose: int = 0, max_turns: int = 50,
    logger: RunLogger | None = None, step_name: str = "",
) -> str:
    """Run Claude in pipe mode with stream-json output and render a live TUI.

    Uses `claude -p --output-format stream-json --include-partial-messages`
    to get real-time streaming events, then renders colored output with
    spinners, fun status verbs, and tool-use summaries.

    Returns the final text output from Claude.
    """
    if logger:
        logger.log_step_start(step_name or activity, prompt=prompt, max_turns=max_turns)

    print()
    print("============================================")
    print(header)
    print("============================================")
    print()

    cmd = [
        "claude", "-p", "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--max-turns", str(max_turns),
        "--verbose",
    ]

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=REPO_PATH,
    )

    # Send prompt and close stdin so Claude starts working
    process.stdin.write(prompt.encode("utf-8"))
    process.stdin.close()

    start_time = time.time()
    spinner_idx = 0
    verb_idx = random.randint(0, len(STATUS_VERBS) - 1)
    last_verb_change = time.time()
    current_state = "thinking"  # thinking | tool_use | text | done
    last_spinner_line = ""
    text_buffer = ""  # accumulate streamed text
    seen_tool_ids = {}  # tool_id -> tool_name, track which we already printed a header for
    final_output = ""  # capture final text for return value

    # Lightweight tracking for the terminal summary (replaces old run_log)
    num_tool_calls = 0
    num_errors = 0
    error_summaries: list[dict] = []
    result_turns = None
    result_usage: dict = {}
    tool_start_times: dict[str, float] = {}  # tool_id -> start time

    def _elapsed() -> str:
        return f"{int(time.time() - start_time)}s"

    def _total_elapsed() -> str:
        if _global_start is not None:
            return f"{int(time.time() - _global_start)}s"
        return _elapsed()

    def _clear_spinner():
        nonlocal last_spinner_line
        if last_spinner_line:
            # Move to start of line and clear it
            sys.stdout.write(f"\r\033[2K")
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

    def _extract_tool_output(block: dict) -> str:
        """Extract full text content from a tool_result block."""
        output = block.get("output", "") or block.get("content", "")
        if isinstance(output, list):
            parts = []
            for b in output:
                if isinstance(b, dict) and b.get("text"):
                    parts.append(b["text"])
            return "\n".join(parts)
        return str(output) if output else ""

    try:
        for line in _iter_stdout_lines(process):
            # Rotate verb every ~5 seconds
            if time.time() - last_verb_change > 5:
                verb_idx = (verb_idx + 1) % len(STATUS_VERBS)
                last_verb_change = time.time()

            if line is None:
                # Heartbeat — show spinner
                _show_spinner(STATUS_VERBS[verb_idx])
                continue

            line = line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if verbose >= 2:
                    _print_line(f"  {_DIM}[raw] {line[:300]}{_RESET}")
                continue

            event_type = event.get("type", "")

            # ============================================================
            # Claude Code stream-json wraps API events in its own envelope.
            # Unwrap: stream_event → event, assistant/user → message, etc.
            # ============================================================

            # --- stream_event: wraps raw API streaming events ---
            if event_type == "stream_event":
                inner = event.get("event", {})
                inner_type = inner.get("type", "")

                if verbose >= 2:
                    _print_line(f"  {_DIM}[stream] {inner_type}{_RESET}")

                if inner_type == "message_start":
                    current_state = "thinking"
                    _show_spinner(STATUS_VERBS[verb_idx])

                elif inner_type == "message_stop":
                    current_state = "done"

                elif inner_type == "content_block_start":
                    block = inner.get("content_block", {})
                    block_type = block.get("type", "")
                    if block_type == "thinking":
                        current_state = "thinking"
                        _show_spinner(STATUS_VERBS[verb_idx])
                    elif block_type == "tool_use":
                        current_state = "tool_use"
                        tool_name = block.get("name", "?")
                        tool_id = block.get("id", "")
                        if tool_id:
                            seen_tool_ids[tool_id] = tool_name
                            tool_start_times[tool_id] = time.time()
                        summary = _format_tool_input(tool_name, block.get("input", {}))
                        _print_line(
                            f"  {_YELLOW}> {tool_name}{summary} ({_elapsed()}){_RESET}"
                        )
                        num_tool_calls += 1
                    elif block_type == "text":
                        current_state = "text"
                        text_buffer = ""

                elif inner_type == "content_block_delta":
                    delta = inner.get("delta", {})
                    delta_type = delta.get("type", "")
                    if delta_type == "thinking_delta":
                        _show_spinner(STATUS_VERBS[verb_idx])
                    elif delta_type == "input_json_delta":
                        _show_spinner(STATUS_VERBS[verb_idx])
                    elif delta_type == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            text_buffer += text

                elif inner_type == "content_block_stop":
                    if current_state == "text" and text_buffer.strip():
                        for tl in text_buffer.strip().splitlines():
                            _print_line(f"  {_CYAN}{tl}{_RESET}")
                        if logger:
                            logger.log_text_block(text_buffer.strip())
                        text_buffer = ""
                    current_state = "thinking"

                continue

            # --- assistant: full/partial assistant message (has complete tool input) ---
            if event_type == "assistant":
                msg = event.get("message", {})
                content_blocks = msg.get("content", [])
                for block in content_blocks:
                    block_type = block.get("type", "")
                    if block_type == "tool_use":
                        tool_id = block.get("id", "")
                        tool_name = block.get("name", "?")
                        tool_input = block.get("input", {})
                        if tool_id and tool_id not in seen_tool_ids:
                            seen_tool_ids[tool_id] = tool_name
                            tool_start_times[tool_id] = time.time()
                            summary = _format_tool_input(tool_name, tool_input)
                            _print_line(
                                f"  {_YELLOW}> {tool_name}{summary} ({_elapsed()}){_RESET}"
                            )
                            num_tool_calls += 1
                        # Log full tool input from the assistant event (complete dict)
                        if logger and tool_id:
                            logger.log_tool_call(tool_id, tool_name, tool_input)
                    elif block_type == "text":
                        text = block.get("text", "")
                        if text.strip() and verbose >= 1:
                            for tl in text.strip().splitlines():
                                _print_line(f"  {_CYAN}{tl}{_RESET}")
                continue

            # --- user: tool results coming back ---
            if event_type == "user":
                msg = event.get("message", {})
                content_blocks = msg.get("content", [])
                for block in content_blocks:
                    block_type = block.get("type", "")
                    if block_type == "tool_result":
                        tool_id = block.get("tool_use_id", "")
                        tool_name = seen_tool_ids.get(tool_id, "?")
                        is_error = block.get("is_error", False)
                        marker = f"{_GREEN}✓" if not is_error else f"\033[31m✗"
                        _print_line(
                            f"  {marker} {tool_name} ({_elapsed()}){_RESET}"
                        )
                        _print_tool_result(block, verbose)

                        # Log full tool output
                        tool_output = _extract_tool_output(block)
                        if logger:
                            logger.log_tool_result(tool_id, tool_name, tool_output, is_error)

                        if is_error:
                            num_errors += 1
                            error_text = tool_output.strip().split("\n")[0][:200] if tool_output else ""
                            error_summaries.append({
                                "name": tool_name,
                                "input_summary": _format_tool_input(tool_name, {}).strip(),
                                "error_text": error_text,
                            })
                continue

            # --- result: final summary at end of run ---
            if event_type == "result":
                _clear_spinner()
                result_data = event.get("result", event)
                duration = _elapsed()
                if isinstance(result_data, dict):
                    turns = result_data.get("num_turns", "?")
                    result_turns = turns
                    result_usage = result_data.get("usage", {})
                    _print_line(
                        f"  {_GREEN}✓ Done. Turns: {turns}, Duration: {duration}, Total: {_total_elapsed()}{_RESET}"
                    )
                    for block in result_data.get("content", []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            final_output += block.get("text", "")
                    if final_output.strip() and verbose >= 1:
                        _print_line(f"  {_DIM}--- Final output ---{_RESET}")
                        for fl in final_output.strip().splitlines():
                            _print_line(f"  {fl}")
                else:
                    # result is a string (final text output)
                    _print_line(
                        f"  {_GREEN}✓ Done. Duration: {duration}, Total: {_total_elapsed()}{_RESET}"
                    )
                    final_output = str(result_data)
                    if final_output.strip() and verbose >= 1:
                        for fl in final_output.strip().splitlines():
                            _print_line(f"  {fl}")
                continue

            # --- system, rate_limit_event, etc.: skip silently ---
            if verbose >= 2:
                _print_line(f"  {_DIM}[skip] type={event_type}{_RESET}")

    except KeyboardInterrupt:
        _clear_spinner()
        print("\n  [interrupted]", flush=True)
    finally:
        _clear_spinner()

    process.wait()
    exit_code = process.returncode
    step_duration = round(time.time() - start_time, 1)

    # Log to RunLogger
    if logger:
        logger.log_claude_result(
            turns=result_turns,
            usage=result_usage,
            output=final_output,
            exit_code=exit_code,
        )
        logger.log_step_end(step_name or activity, exit_code=exit_code)

    # Print end-of-run summary (kept for human watching the terminal)
    turns_display = result_turns or "?"

    _clear_spinner()
    print()
    print(f"  ── Summary ──────────────────────────")
    print(f"  Tool calls: {num_tool_calls} ({num_errors} errors)")
    print(f"  Turns: {turns_display} | Duration: {step_duration}s")
    if result_usage:
        in_tok = result_usage.get("input_tokens", 0)
        out_tok = result_usage.get("output_tokens", 0)
        print(f"  Tokens: {in_tok:,} in / {out_tok:,} out")
    if num_errors > 0:
        print(f"  Errors:")
        for err in error_summaries:
            err_name = err.get("name", "?")
            err_summary = err.get("input_summary", "")
            err_text = err.get("error_text", "")
            print(f"    ✗ {err_name} {err_summary} ({err_text})")
    print(f"  ────────────────────────────────────")
    print()

    # Capture stderr for diagnostics
    stderr_output = ""
    try:
        stderr_output = process.stderr.read().decode("utf-8", errors="replace").strip()
    except Exception:
        pass

    print(f"  [claude exit code: {exit_code}]")
    if stderr_output and (exit_code != 0 or verbose >= 2):
        print(f"  [stderr] {stderr_output[:500]}")
    if exit_code != 0:
        raise SystemExit(exit_code)

    return final_output


def run_claude_work(issue: Issue, repo: str, guidelines: str, verbose: int = 0, logger: RunLogger | None = None) -> None:
    """Run Claude to work on the issue with streaming TUI output."""
    prompt = _build_work_prompt(issue, repo, guidelines)
    _run_claude_streaming(
        prompt=prompt,
        header="[7/9] Claude is working on the issue...",
        activity="implementing",
        verbose=verbose,
        max_turns=50,
        logger=logger,
        step_name="work",
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


def run_claude_review(default_branch: str, verbose: int = 0, logger: RunLogger | None = None) -> None:
    """Run Claude self-review with streaming TUI. Exits with error if review is REJECTED."""
    review_prompt = f"""\
Review the changes you just made (use `git diff upstream/{default_branch}` to see them against the base branch).

**Environment:**
- Working directory: /workspace/repo (the cloned repository)
- Python: use `python3` (not `python`). There is no `python` alias.
- Installing packages: use `python3 -m pip install --break-system-packages <pkg>` or create a venv first.

Check for:
- Correctness: Does the implementation actually address the issue?
- Test coverage: Are there tests for the new behavior? If the project has tests, did you add/update them?
- Code style: Is it consistent with the rest of the codebase?
- Security: No secrets, no injection vulnerabilities, no unsafe operations.
- Scope: No unrelated changes or unnecessary refactoring.

If you find issues, fix them now.

After your review, output exactly one of:
- APPROVED — if the changes are ready for a PR.
- REJECTED: <reason> — if the changes have unfixable problems."""

    output = _run_claude_streaming(
        prompt=review_prompt,
        header="[8.5/9] Claude is self-reviewing...",
        activity="reviewing",
        verbose=verbose,
        max_turns=50,
        logger=logger,
        step_name="review",
    )

    if output.strip() and output.strip().split('\n')[-1].strip().startswith("REJECTED"):
        print()
        print("=== SELF-REVIEW REJECTED ===")
        raise SystemExit(1)

    print()
    print("=== SELF-REVIEW APPROVED ===")


def generate_pr_description(issue: Issue, repo: str, default_branch: str) -> tuple[str, str]:
    """Ask Claude to generate a PR title and body for the changes made.

    Returns (title, body). Falls back to pr_template if Claude fails.
    """
    prompt = (
        f"Write a GitHub PR title and body for the changes you just made "
        f"(run `git diff upstream/{default_branch}` to see them). "
        f"The PR addresses issue #{issue.number}: {issue.title}. "
        f"Output format: first line = title, blank line, then body in markdown.\n\n"
        f"**Environment:**\n"
        f"- Working directory: /workspace/repo (the cloned repository)\n"
        f"- Python: use `python3` (not `python`). There is no `python` alias.\n"
        f"- Installing packages: use `python3 -m pip install --break-system-packages <pkg>` or create a venv first."
    )

    result = subprocess.run(
        ["claude", "-p", "--dangerously-skip-permissions", "--model", "haiku"],
        input=prompt,
        capture_output=True,
        text=True,
        cwd=REPO_PATH,
    )

    if result.returncode == 0 and result.stdout.strip():
        output = result.stdout.strip()
        parts = output.split('\n', 1)
        title = parts[0].strip()
        body = parts[1].strip() if len(parts) > 1 else ""
        if title:
            return title, body

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
    query = "&".join(f"{k}={quote(v)}" for k, v in params.items())
    url = f"https://github.com/{repo}/compare/{default_branch}...{head}?{query}"

    if len(url) > 8000:
        params.pop("body")
        query = "&".join(f"{k}={quote(v)}" for k, v in params.items())
        url = f"https://github.com/{repo}/compare/{default_branch}...{head}?{query}"
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
        "donate your excess Claude Code credits to solve open-source issues.\n\n"
        "Klaus Kode is not affiliated with or endorsed by Claude Code / Anthropic.\n\n"
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
