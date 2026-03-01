"""Tests for klaus_kode.pr_description — _build_compare_url (pure function)."""

from __future__ import annotations

from klaus_kode.pr_description import _build_compare_url


class TestBuildCompareUrl:
    def test_returns_valid_url(self):
        url, body_note = _build_compare_url(
            title="Fix #42: bug",
            body="Short body",
            repo="owner/repo",
            head="fork-owner:fix/issue-42",
            default_branch="main",
            pr_file="/workspace/pr_description.md",
        )
        assert url.startswith("https://github.com/owner/repo/compare/main...")
        assert "fork-owner" in url
        assert "Fix" in url

    def test_short_body_no_note(self):
        url, body_note = _build_compare_url(
            title="Fix",
            body="Short",
            repo="o/r",
            head="f:b",
            default_branch="main",
            pr_file="/pr.md",
        )
        assert body_note == ""

    def test_long_body_triggers_note(self):
        # Build a body that makes the URL > 8000 chars
        long_body = "x" * 9000
        url, body_note = _build_compare_url(
            title="Fix",
            body=long_body,
            repo="o/r",
            head="f:b",
            default_branch="main",
            pr_file="/pr.md",
        )
        assert body_note != ""
        assert "/pr.md" in body_note
        # URL should NOT contain the long body
        assert len(url) < 8500
