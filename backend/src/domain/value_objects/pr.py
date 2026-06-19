"""
src/domain/value_objects/pr.py — Pull Request value objects.

These value objects represent the state a GoalAggregate observes from GitHub.
They are always derived from GitHubPort data; the orchestrator never writes
them directly — only SyncGoalPRStatusUseCase may update them via aggregate
transition methods.

PRStatus     — overall PR open/closed/merged state
PRCheckConclusion — per-check-run outcome
PRInfo       — snapshot of a PR's full observable state
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class PRStatus(str, Enum):
    """High-level state of the GitHub Pull Request."""

    OPEN   = "open"    # PR is open and active
    CLOSED = "closed"  # PR closed without merging
    MERGED = "merged"  # PR was merged into base


class PRCheckConclusion(str, Enum):
    """Outcome of a single GitHub check run."""

    SUCCESS    = "success"
    FAILURE    = "failure"
    NEUTRAL    = "neutral"
    CANCELLED  = "cancelled"
    SKIPPED    = "skipped"
    TIMED_OUT  = "timed_out"
    ACTION_REQUIRED = "action_required"
    PENDING    = "pending"   # check in progress (no conclusion yet)


class CheckRunResult(BaseModel):
    """Result snapshot for a single GitHub Actions check run."""

    name: str
    conclusion: PRCheckConclusion
    url: Optional[str] = None

    model_config = {"frozen": True}

    @property
    def passed(self) -> bool:
        return self.conclusion in (
            PRCheckConclusion.SUCCESS,
            PRCheckConclusion.NEUTRAL,
            PRCheckConclusion.SKIPPED,
        )


class PRInfo(BaseModel):
    """
    Immutable snapshot of a GitHub PR's observable state.

    Populated by GitHubPort.get_pr_info() and consumed by
    SyncGoalPRStatusUseCase and AdvanceGoalFromPRUseCase.
    """

    pr_number:   int
    status:      PRStatus
    head_branch: str
    base_branch: str
    head_sha:    str
    html_url:    str
    title:       str
    check_runs:  list[CheckRunResult] = []
    approval_count: int = 0
    changes_requested: bool = False

    model_config = {"frozen": True}

    def all_required_checks_passed(self, required_checks: list[str]) -> bool:
        """
        Return True if every required check name is present and passed.

        If required_checks is empty, returns True (no gate configured).
        """
        if not required_checks:
            return True

        check_map = {r.name: r for r in self.check_runs}
        for name in required_checks:
            result = check_map.get(name)
            if result is None or not result.passed:
                return False
        return True

    def meets_approval_gate(self, min_approvals: int) -> bool:
        """Return True if approval count ≥ min_approvals and no changes requested."""
        if self.changes_requested:
            return False
        return self.approval_count >= min_approvals

    def is_ci_green(self, required_checks: list[str]) -> bool:
        """Convenience: checks passed + no pending required runs."""
        if not required_checks:
            return True
        check_map = {r.name: r for r in self.check_runs}
        for name in required_checks:
            result = check_map.get(name)
            if result is None:
                return False  # not yet reported
            if result.conclusion == PRCheckConclusion.PENDING:
                return False
            if not result.passed:
                return False
        return True
