"""Tests for klaus_kode.claude_runner — backwards-compatibility facade."""

from __future__ import annotations

import klaus_kode.claude_runner as facade
import klaus_kode.repo_ops as repo_ops
import klaus_kode.selection as selection


class TestFacadeReExports:
    def test_repo_path_accessible(self):
        assert hasattr(facade, "REPO_PATH")
        assert facade.REPO_PATH == repo_ops.REPO_PATH

    def test_pick_issue_same_function(self):
        assert facade.pick_issue is selection.pick_issue

    def test_clone_repo_same_function(self):
        assert facade.clone_repo is repo_ops.clone_repo

    def test_cleanup_inner_claude_md_callable(self):
        assert callable(facade._cleanup_inner_claude_md)

    def test_global_start_exists(self):
        assert hasattr(facade, "_global_start")
