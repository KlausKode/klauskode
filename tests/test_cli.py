"""Tests for klaus_kode.cli — argument parsing only, NO pipeline execution."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestCliArgParsing:
    def test_repo_and_issue_parses(self):
        """--repo and --issue should parse without error when pipeline is mocked.

        Session.load returns a MagicMock where is_completed() is truthy,
        so all pipeline steps are skipped and main() returns normally.
        """
        with patch("klaus_kode.cli.RunLogger"), \
             patch("klaus_kode.cli.Session.load"):
            from klaus_kode.cli import main
            # Should complete without error (all steps skipped via mock session)
            main(["--repo", "a/b", "--issue", "1"])

    def test_repo_and_find_repo_together_errors(self):
        """--repo and --find-repo together should cause SystemExit(2) from argparse."""
        with pytest.raises(SystemExit) as exc_info:
            from klaus_kode.cli import main
            main(["--repo", "a/b", "--find-repo", "python"])
        assert exc_info.value.code == 2

    def test_neither_repo_nor_find_repo_errors(self):
        """No --repo and no --find-repo should cause SystemExit(2) from argparse."""
        with pytest.raises(SystemExit) as exc_info:
            from klaus_kode.cli import main
            main([])
        assert exc_info.value.code == 2
