"""Structured JSONL run logger for AI-debuggability.

Emits one JSON object per line to a file at /workspace/logs/run_TIMESTAMP_RUNID.jsonl.
At run end, also prints all entries between marker lines as a fallback if the volume
mount is missing.
"""

from __future__ import annotations

import datetime
import json
import os
import time
import uuid


# Cap subprocess stdout/stderr at 10 KB to avoid bloating the log
_MAX_SUBPROCESS_OUTPUT = 10 * 1024

_JSONL_START_MARKER = "===KLAUS_KODE_JSONL_START==="
_JSONL_END_MARKER = "===KLAUS_KODE_JSONL_END==="


class RunLogger:
    """Line-buffered JSONL logger for a single klaus-kode run."""

    def __init__(self, log_dir: str = "/workspace/logs") -> None:
        self.run_id = uuid.uuid4().hex[:8]
        self._start_time = time.time()
        self._context: dict = {}
        self._current_step: str | None = None
        self._step_start_time: float | None = None
        self._entries: list[str] = []  # raw JSON lines for final dump

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_dir = log_dir
        self._log_path = os.path.join(log_dir, f"run_{ts}_{self.run_id}.jsonl")
        self._file = None

        try:
            os.makedirs(log_dir, exist_ok=True)
            self._file = open(self._log_path, "w", buffering=1)  # line-buffered
        except OSError:
            # Volume mount may be missing — entries will be dumped to stdout at end
            self._file = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit(self, entry: dict) -> None:
        """Write a single JSON line to the log file and buffer."""
        entry["run_id"] = self.run_id
        entry["timestamp"] = datetime.datetime.now().isoformat()
        entry["elapsed_s"] = round(time.time() - self._start_time, 2)
        if self._current_step:
            entry.setdefault("step", self._current_step)
        line = json.dumps(entry, default=str)
        self._entries.append(line)
        if self._file is not None:
            try:
                self._file.write(line + "\n")
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Public event methods
    # ------------------------------------------------------------------

    def log_run_start(self, args: dict) -> None:
        self._emit({"type": "run_start", "args": args})

    def set_context(self, **kw) -> None:
        self._context.update(kw)
        self._emit({"type": "context_update", "context": dict(self._context)})

    def log_step_start(self, name: str, prompt: str = "", max_turns: int | None = None) -> None:
        self._current_step = name
        self._step_start_time = time.time()
        entry: dict = {"type": "step_start", "step_name": name}
        if prompt:
            entry["prompt"] = prompt
        if max_turns is not None:
            entry["max_turns"] = max_turns
        self._emit(entry)

    def log_tool_call(self, tool_id: str, name: str, input_data: dict) -> None:
        self._emit({
            "type": "tool_call",
            "tool_id": tool_id,
            "tool_name": name,
            "tool_input": input_data,
        })

    def log_tool_result(self, tool_id: str, name: str, output: str, is_error: bool = False) -> None:
        self._emit({
            "type": "tool_result",
            "tool_id": tool_id,
            "tool_name": name,
            "tool_output": output,
            "is_error": is_error,
        })

    def log_text_block(self, text: str) -> None:
        self._emit({"type": "text_block", "text": text})

    def log_claude_result(
        self,
        turns: int | str | None = None,
        usage: dict | None = None,
        output: str = "",
        exit_code: int | None = None,
    ) -> None:
        self._emit({
            "type": "claude_result",
            "num_turns": turns,
            "token_usage": usage or {},
            "output": output,
            "exit_code": exit_code,
        })

    def log_step_end(self, name: str, exit_code: int | None = None) -> None:
        duration = None
        if self._step_start_time is not None:
            duration = round(time.time() - self._step_start_time, 2)
        self._emit({
            "type": "step_end",
            "step_name": name,
            "step_duration_s": duration,
            "exit_code": exit_code,
        })
        self._current_step = None
        self._step_start_time = None

    def log_subprocess(
        self,
        cmd: list[str] | str,
        returncode: int | None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self._emit({
            "type": "subprocess",
            "cmd": cmd if isinstance(cmd, str) else " ".join(cmd),
            "returncode": returncode,
            "stdout": stdout[:_MAX_SUBPROCESS_OUTPUT],
            "stderr": stderr[:_MAX_SUBPROCESS_OUTPUT],
        })

    def log_decision(self, decision: str, reason: str, **kw) -> None:
        entry = {"type": "decision", "decision": decision, "reason": reason}
        entry.update(kw)
        self._emit(entry)

    def log_error(self, error: str | Exception) -> None:
        self._emit({"type": "error", "error": str(error)})

    def log_run_end(self, **kw) -> None:
        entry: dict = {
            "type": "run_end",
            "total_duration_s": round(time.time() - self._start_time, 2),
        }
        entry.update(kw)
        self._emit(entry)

    # ------------------------------------------------------------------
    # Finalisation
    # ------------------------------------------------------------------

    def flush_final_summary(self) -> None:
        """Print all entries between markers (fallback if volume mount missing) and close."""
        print(f"\n{_JSONL_START_MARKER}")
        for line in self._entries:
            print(line)
        print(f"{_JSONL_END_MARKER}\n")

        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None
