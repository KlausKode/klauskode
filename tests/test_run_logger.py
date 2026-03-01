"""Tests for klaus_kode.run_logger — structured JSONL logging."""

from __future__ import annotations

import json
import os

from klaus_kode.run_logger import RunLogger


def _read_entries(log_path: str) -> list[dict]:
    """Read all JSON lines from a log file."""
    with open(log_path) as f:
        return [json.loads(line) for line in f if line.strip()]


class TestRunLogger:
    def test_creates_log_file(self, tmp_path):
        logger = RunLogger(log_dir=str(tmp_path))
        assert logger._file is not None
        assert os.path.exists(logger._log_path)
        logger.flush_final_summary()

    def test_log_run_start(self, tmp_path):
        logger = RunLogger(log_dir=str(tmp_path))
        logger.log_run_start({"repo": "a/b"})
        logger.flush_final_summary()
        entries = _read_entries(logger._log_path)
        assert any(e["type"] == "run_start" for e in entries)

    def test_log_step_start_and_end(self, tmp_path):
        logger = RunLogger(log_dir=str(tmp_path))
        logger.log_step_start("test_step")
        logger.log_step_end("test_step")
        logger.flush_final_summary()
        entries = _read_entries(logger._log_path)
        types = [e["type"] for e in entries]
        assert "step_start" in types
        assert "step_end" in types
        end_entry = next(e for e in entries if e["type"] == "step_end")
        assert end_entry["step_name"] == "test_step"
        assert end_entry["step_duration_s"] is not None

    def test_log_tool_call(self, tmp_path):
        logger = RunLogger(log_dir=str(tmp_path))
        logger.log_tool_call("t1", "Read", {"file_path": "/foo"})
        logger.flush_final_summary()
        entries = _read_entries(logger._log_path)
        tc = next(e for e in entries if e["type"] == "tool_call")
        assert tc["tool_name"] == "Read"
        assert tc["tool_id"] == "t1"

    def test_log_tool_result(self, tmp_path):
        logger = RunLogger(log_dir=str(tmp_path))
        logger.log_tool_result("t1", "Read", "file content", is_error=False)
        logger.flush_final_summary()
        entries = _read_entries(logger._log_path)
        tr = next(e for e in entries if e["type"] == "tool_result")
        assert tr["tool_output"] == "file content"
        assert tr["is_error"] is False

    def test_log_error(self, tmp_path):
        logger = RunLogger(log_dir=str(tmp_path))
        logger.log_error("something went wrong")
        logger.flush_final_summary()
        entries = _read_entries(logger._log_path)
        err = next(e for e in entries if e["type"] == "error")
        assert err["error"] == "something went wrong"

    def test_log_run_end_includes_duration(self, tmp_path):
        logger = RunLogger(log_dir=str(tmp_path))
        logger.log_run_end(exit_code=0)
        logger.flush_final_summary()
        entries = _read_entries(logger._log_path)
        end = next(e for e in entries if e["type"] == "run_end")
        assert "total_duration_s" in end

    def test_flush_dumps_to_stdout_when_no_file(self, tmp_path, capsys):
        # Create logger with a read-only dir to force _file=None
        readonly_dir = str(tmp_path / "nonexistent" / "deep" / "path")
        # Don't create it — let the OSError trigger
        logger = RunLogger.__new__(RunLogger)
        logger.run_id = "test123"
        logger._start_time = __import__("time").time()
        logger._context = {}
        logger._current_step = None
        logger._step_start_time = None
        logger._entries = []
        logger._log_dir = readonly_dir
        logger._log_path = os.path.join(readonly_dir, "test.jsonl")
        logger._file = None

        logger.log_error("test error")
        logger.flush_final_summary()
        captured = capsys.readouterr().out
        assert "KLAUS_KODE_JSONL_START" in captured
        assert "test error" in captured
