"""Tests for klaus_kode.context — Session persistence and PipelineContext."""

from __future__ import annotations

import json
import os

from klaus_kode.context import PipelineContext, Session


class TestSession:
    def test_empty_completed_steps(self):
        s = Session()
        assert s.completed_steps == []

    def test_is_completed_false_initially(self):
        s = Session()
        assert s.is_completed("x") is False

    def test_mark_then_is_completed(self, tmp_path):
        s = Session(session_file=str(tmp_path / "session.json"))
        s.mark_completed("x")
        assert s.is_completed("x") is True

    def test_mark_completed_with_outputs(self, tmp_path):
        s = Session(session_file=str(tmp_path / "session.json"))
        s.mark_completed("step1", {"key": "val"})
        assert s.step_outputs["step1"] == {"key": "val"}

    def test_mark_completed_idempotent(self, tmp_path):
        s = Session(session_file=str(tmp_path / "session.json"))
        s.mark_completed("x")
        s.mark_completed("x")
        assert s.completed_steps.count("x") == 1

    def test_save_and_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "session.json")
        s = Session(session_file=path)
        s.mark_completed("a", {"result": 42})
        s.mark_completed("b")

        loaded = Session.load(path)
        assert loaded.is_completed("a")
        assert loaded.is_completed("b")
        assert loaded.step_outputs["a"]["result"] == 42

    def test_load_missing_file_returns_fresh(self, tmp_path):
        loaded = Session.load(str(tmp_path / "nonexistent.json"))
        assert loaded.completed_steps == []

    def test_load_corrupt_json_returns_fresh(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{invalid json!!!")
        loaded = Session.load(str(path))
        assert loaded.completed_steps == []


class TestPipelineContext:
    def test_defaults(self):
        ctx = PipelineContext()
        assert ctx.verbose == 0
        assert ctx.start_time > 0
        assert ctx.repo is None

    def test_explicit_args(self):
        ctx = PipelineContext(repo="a/b", verbose=2)
        assert ctx.repo == "a/b"
        assert ctx.verbose == 2
