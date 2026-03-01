"""Tests for klaus_kode.prompts — prompt builders and constants."""

from __future__ import annotations

from klaus_kode.github import Issue
from klaus_kode.prompts import (
    DISALLOWED_TOOLS,
    REVIEW_TOOLS,
    REVIEWER_SYSTEM_PROMPT,
    WORK_TOOLS,
    WORKER_SYSTEM_PROMPT,
    build_review_prompt,
    build_work_prompt,
)


def _make_issue(**kwargs):
    defaults = dict(number=42, title="Fix bug", body="Bug description", labels=["bug"])
    defaults.update(kwargs)
    return Issue(**defaults)


class TestBuildWorkPrompt:
    def test_contains_issue_info(self):
        issue = _make_issue()
        result = build_work_prompt(issue, "owner/repo", "")
        assert "#42" in result
        assert "Fix bug" in result
        assert "Bug description" in result

    def test_includes_guidelines(self):
        issue = _make_issue()
        result = build_work_prompt(issue, "owner/repo", "Please run black")
        assert "Contributing guidelines" in result
        assert "Please run black" in result

    def test_no_guidelines_section_when_empty(self):
        issue = _make_issue()
        result = build_work_prompt(issue, "owner/repo", "")
        assert "Contributing guidelines" not in result

    def test_includes_repo_context(self):
        issue = _make_issue()
        result = build_work_prompt(issue, "owner/repo", "", repo_context="file tree here")
        assert "Repository context" in result
        assert "file tree here" in result

    def test_no_context_section_when_empty(self):
        issue = _make_issue()
        result = build_work_prompt(issue, "owner/repo", "", repo_context="")
        assert "Repository context" not in result


class TestBuildReviewPrompt:
    def test_with_diff_includes_it(self):
        result = build_review_prompt("main", diff_output="diff here")
        assert "diff here" in result
        assert "```" in result

    def test_without_diff_includes_git_instruction(self):
        result = build_review_prompt("main")
        assert "git diff upstream/main" in result


class TestConstants:
    def test_work_tools_non_empty(self):
        assert len(WORK_TOOLS) > 0

    def test_review_tools_non_empty(self):
        assert len(REVIEW_TOOLS) > 0

    def test_disallowed_tools_non_empty(self):
        assert len(DISALLOWED_TOOLS) > 0

    def test_worker_system_prompt_non_empty(self):
        assert len(WORKER_SYSTEM_PROMPT) > 0

    def test_reviewer_system_prompt_non_empty(self):
        assert len(REVIEWER_SYSTEM_PROMPT) > 0
