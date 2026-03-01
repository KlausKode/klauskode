"""PR title and body formatting."""

from __future__ import annotations

from klaus_kode.github import Issue


def format_pr_title(issue: Issue) -> str:
    """Generate a PR title from the issue."""
    return f"Fix #{issue.number}: {issue.title}"


def format_pr_body(issue: Issue, repo: str = "") -> str:
    """Generate the PR body with attribution and opt-out info."""
    repo_escaped = repo.replace("/", "%2F")

    return f"""\
## Summary

Automated fix for #{issue.number}: {issue.title}

Fixes #{issue.number}

## What does this PR do?

This PR addresses issue #{issue.number} by implementing the fix described in the issue.
"""
