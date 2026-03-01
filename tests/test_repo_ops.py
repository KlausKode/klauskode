"""Tests for klaus_kode.repo_ops — filesystem operations with mocked subprocess."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import klaus_kode.repo_ops as repo_ops


class TestReadContributingGuidelines:
    def test_with_contributing_md(self, tmp_workspace):
        (tmp_workspace / "CONTRIBUTING.md").write_text("# Contributing\nPlease run tests.")
        result = repo_ops.read_contributing_guidelines()
        assert "Please run tests" in result

    def test_no_guideline_files(self, tmp_workspace):
        result = repo_ops.read_contributing_guidelines()
        assert result == ""


class TestWriteInnerClaudeMd:
    def test_creates_claude_md(self, tmp_workspace, mock_issue):
        repo_ops.write_inner_claude_md(mock_issue, "owner/repo", "guidelines", "fix/issue-42")
        claude_md = tmp_workspace / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text()
        assert "#42" in content
        assert "Fix bug" in content
        assert "fix/issue-42" in content


class TestCleanupInnerClaudeMd:
    def test_removes_claude_md(self, tmp_workspace):
        claude_md = tmp_workspace / "CLAUDE.md"
        claude_md.write_text("temporary")
        repo_ops.cleanup_inner_claude_md()
        assert not claude_md.exists()

    def test_no_error_when_missing(self, tmp_workspace):
        # Should not raise
        repo_ops.cleanup_inner_claude_md()


class TestGatherRepoContext:
    def test_reads_readme(self, tmp_workspace):
        (tmp_workspace / "README.md").write_text("# My Project\nSome description.")

        # Mock subprocess.run for the `find` call
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "./README.md\n./src/main.py\n"

        with patch("klaus_kode.repo_ops.subprocess.run", return_value=mock_result):
            result = repo_ops.gather_repo_context()

        assert "My Project" in result
        assert "README.md" in result


class TestCommitChanges:
    def test_no_changes_no_commits(self, tmp_workspace):
        """When git status is empty and no commits exist, returns False."""
        call_count = 0

        def mock_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""  # Empty status / empty log
            result.stderr = ""
            return result

        with patch("klaus_kode.repo_ops.subprocess.run", side_effect=mock_run):
            has_changes = repo_ops.commit_changes(42, "main")

        assert has_changes is False

    def test_existing_commits_returns_true(self, tmp_workspace):
        """When status is empty but commits exist, returns True."""
        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if "status" in cmd:
                result.stdout = ""  # No uncommitted changes
            elif "log" in cmd:
                result.stdout = "abc1234 fix something"  # Existing commit
            else:
                result.stdout = ""
            return result

        with patch("klaus_kode.repo_ops.subprocess.run", side_effect=mock_run):
            has_changes = repo_ops.commit_changes(42, "main")

        assert has_changes is True
