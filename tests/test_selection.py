"""Tests for klaus_kode.selection — AI selection with mocked _quick_claude."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from klaus_kode.github import Issue, Repository


def _make_issues():
    return [
        Issue(number=1, title="Easy bug", body="Simple fix", labels=["bug"]),
        Issue(number=2, title="Hard feature", body="Complex feature", labels=["enhancement"]),
        Issue(number=3, title="Docs update", body="Fix typo", labels=["docs"]),
    ]


def _make_repos():
    return [
        Repository("org/alpha", "Alpha project", "Python", 50, 5, ["web"]),
        Repository("org/beta", "Beta project", "Rust", 200, 10, ["cli"]),
    ]


class TestPickIssue:
    def test_valid_response_returns_matching_issue(self):
        from klaus_kode.selection import pick_issue
        issues = _make_issues()
        # Mock _quick_claude to return JSON selecting issue #2
        mock_coro = AsyncMock(return_value=json.dumps({"issue_number": 2}))
        with patch("klaus_kode.selection._quick_claude", mock_coro):
            result = pick_issue(issues, "hard feature")
        assert result.number == 2

    def test_exception_falls_back_to_first(self):
        from klaus_kode.selection import pick_issue
        issues = _make_issues()
        mock_coro = AsyncMock(side_effect=Exception("API error"))
        with patch("klaus_kode.selection._quick_claude", mock_coro):
            result = pick_issue(issues, "anything")
        assert result.number == 1


class TestPickRepo:
    def test_valid_response_returns_matching_repo(self):
        from klaus_kode.selection import pick_repo
        repos = _make_repos()
        # Index is 1-based, so 2 means the second repo
        mock_coro = AsyncMock(return_value=json.dumps({"repo_index": 2}))
        with patch("klaus_kode.selection._quick_claude", mock_coro):
            result = pick_repo(repos, "rust cli tool")
        assert result.full_name == "org/beta"


class TestSuggestBranchName:
    def test_no_guidelines_returns_fallback(self):
        from klaus_kode.selection import suggest_branch_name
        issue = Issue(number=42, title="Fix bug", body="desc", labels=[])
        result = suggest_branch_name(issue, "")
        assert result == "fix/issue-42"
