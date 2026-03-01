"""PipelineContext and Session: centralized state for the pipeline."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from klaus_kode.github import Issue, Repository
from klaus_kode.run_logger import RunLogger


@dataclass
class Session:
    """Minimal session persistence for resumable pipelines.

    Tracks which pipeline steps have completed and their outputs,
    allowing the pipeline to resume from the last successful step.
    """

    session_file: str = "/workspace/session.json"
    completed_steps: list[str] = field(default_factory=list)
    step_outputs: dict[str, Any] = field(default_factory=dict)

    def is_completed(self, step_name: str) -> bool:
        """Check if a step has already been completed."""
        return step_name in self.completed_steps

    def mark_completed(self, step_name: str, outputs: dict | None = None) -> None:
        """Mark a step as completed and optionally store its outputs."""
        if step_name not in self.completed_steps:
            self.completed_steps.append(step_name)
        if outputs:
            self.step_outputs[step_name] = outputs
        self.save()

    def save(self) -> None:
        """Persist session state to disk."""
        try:
            os.makedirs(os.path.dirname(self.session_file) or ".", exist_ok=True)
            data = {
                "completed_steps": self.completed_steps,
                "step_outputs": self.step_outputs,
            }
            with open(self.session_file, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except OSError:
            pass

    @classmethod
    def load(cls, path: str = "/workspace/session.json") -> Session:
        """Load session state from disk, or return a fresh session."""
        try:
            with open(path) as f:
                data = json.load(f)
            session = cls(session_file=path)
            session.completed_steps = data.get("completed_steps", [])
            session.step_outputs = data.get("step_outputs", {})
            return session
        except (OSError, json.JSONDecodeError):
            return cls(session_file=path)


@dataclass
class PipelineContext:
    """Centralized state for the entire pipeline, replacing all globals.

    All pipeline steps read from and write to this context object.
    """

    # From CLI args
    repo: str | None = None
    find_repo: str | None = None
    issue_number: int | None = None
    find_description: str | None = None
    verbose: int = 0
    max_budget_usd: float | None = None

    # Computed during pipeline
    start_time: float = field(default_factory=time.time)
    candidates_repos: list[Repository] | None = None
    issue: Issue | None = None
    fork: str | None = None
    default_branch: str | None = None
    branch_name: str | None = None
    guidelines: str = ""
    repo_context: str = ""
    diff_output: str = ""
    pr_title: str = ""
    pr_body: str = ""

    # Infrastructure
    logger: RunLogger = field(default_factory=RunLogger)
    session: Session = field(default_factory=Session)
