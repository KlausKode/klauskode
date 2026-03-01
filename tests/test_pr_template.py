"""Tests for klaus_kode.pr_template — static PR formatting."""

from __future__ import annotations

from klaus_kode.github import Issue
from klaus_kode.pr_template import format_pr_body, format_pr_title


def _make_issue(**kwargs):
    defaults = dict(number=42, title="Fix bug", body="Bug desc", labels=["bug"])
    defaults.update(kwargs)
    return Issue(**defaults)


class TestFormatPrTitle:
    def test_basic(self):
        issue = _make_issue()
        assert format_pr_title(issue) == "Fix #42: Fix bug"

    def test_different_number(self):
        issue = _make_issue(number=99, title="Update docs")
        assert format_pr_title(issue) == "Fix #99: Update docs"


class TestFormatPrBody:
    def test_contains_issue_number(self):
        issue = _make_issue()
        body = format_pr_body(issue)
        assert "#42" in body

    def test_with_repo(self):
        issue = _make_issue()
        body = format_pr_body(issue, repo="owner/repo")
        # Body is generated regardless of repo arg
        assert "#42" in body
