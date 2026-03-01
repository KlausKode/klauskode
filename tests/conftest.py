"""Shared test fixtures for klaus_kode test suite.

Safety: Every fixture here ensures NO real external calls (GitHub, Claude, git push)
ever happen during testing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import types
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Mock claude_agent_sdk at module level BEFORE any test module is collected.
# This must happen before pytest imports test files that transitively import
# claude_sdk.py which does `from claude_agent_sdk import ...`.
# ---------------------------------------------------------------------------

_fake_sdk = types.ModuleType("claude_agent_sdk")
_fake_sdk.AssistantMessage = type("AssistantMessage", (), {})
_fake_sdk.ClaudeAgentOptions = type("ClaudeAgentOptions", (), {})
_fake_sdk.ResultMessage = type("ResultMessage", (), {})
_fake_sdk.SystemMessage = type("SystemMessage", (), {})
_fake_sdk.TextBlock = type("TextBlock", (), {"text": ""})
_fake_sdk.ToolResultBlock = type("ToolResultBlock", (), {})
_fake_sdk.ToolUseBlock = type("ToolUseBlock", (), {})
_fake_sdk.query = MagicMock()

sys.modules["claude_agent_sdk"] = _fake_sdk


# ---------------------------------------------------------------------------
# Safety net: block real gh / git-push calls at subprocess level
# ---------------------------------------------------------------------------

_real_subprocess_run = subprocess.run


def _guarded_subprocess_run(*args, **kwargs):
    """Raise RuntimeError if a test accidentally calls gh or git push."""
    cmd = args[0] if args else kwargs.get("args", [])
    if isinstance(cmd, (list, tuple)):
        if cmd and cmd[0] == "gh":
            raise RuntimeError(
                f"SAFETY: test tried to run gh command: {cmd}"
            )
        if len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "push":
            raise RuntimeError(
                f"SAFETY: test tried to run git push: {cmd}"
            )
    return _real_subprocess_run(*args, **kwargs)


@pytest.fixture(autouse=True, scope="session")
def _block_dangerous_subprocesses(request):
    """Session-wide guard that prevents accidental gh / git push calls."""
    subprocess.run = _guarded_subprocess_run
    yield
    subprocess.run = _real_subprocess_run


# ---------------------------------------------------------------------------
# Domain fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_issue():
    """Return a test Issue instance."""
    from klaus_kode.github import Issue
    return Issue(
        number=42,
        title="Fix bug",
        body="There is a bug in the parser that causes crashes on empty input.",
        labels=["bug"],
    )


@pytest.fixture
def mock_repo():
    """Return a test Repository instance."""
    from klaus_kode.github import Repository
    return Repository(
        full_name="owner/repo",
        description="A test repository",
        language="Python",
        stars=100,
        open_issues_count=10,
        topics=["testing"],
    )


# ---------------------------------------------------------------------------
# Temporary workspace that simulates /workspace/repo
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """Create a temp dir simulating /workspace/repo with .git/info/ structure.

    Monkeypatches repo_ops.REPO_PATH to point to the temp dir.
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    git_info = repo_dir / ".git" / "info"
    git_info.mkdir(parents=True)
    (git_info / "exclude").write_text("")

    import klaus_kode.repo_ops as repo_ops
    monkeypatch.setattr(repo_ops, "REPO_PATH", str(repo_dir))

    return repo_dir
