"""
src/domain/aggregates/goal.py — GoalAggregate.

The Goal is the authoritative unit of a software objective. It owns a
dependency graph of tasks and their collective progress toward a single
outcome: all task branches merged into the goal branch, then validated
through a GitHub PR + CI gate before landing on main.

Lifecycle (PR-driven flow):
    PENDING
      │ start()
      ▼
    RUNNING  ─(all task branches merged into goal branch)─▶  READY_FOR_REVIEW
                                                                    │ open_pr()
                                                                    ▼
                                                         AWAITING_PR_APPROVAL
                                                           │            │
                                           ci + approvals ┘            │ new commits/CI fail
                                                                (regression → stays here)
                                                                ▼
                                                            APPROVED
                                                                │ PR merged
                                                                ▼
                                                            MERGED

    FAILED: task canceled OR PR closed unmerged (from any PR-phase state)

Invariants:
  - pr_number is set exactly once via open_pr().
  - PR state fields are updated only via sync_pr_state().
  - Only the orchestrator/use-cases call PR transition methods.
  - Agents have NO access to PR methods.
  - GitHub is the single source of truth for merge state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from src.domain.value_objects.status import TaskStatus
from src.domain.value_objects.task import HistoryEntry


class GoalStatus(str, Enum):
    # Task-execution phase
    PENDING   = "pending"
    RUNNING   = "running"

    # PR review phase
    READY_FOR_REVIEW     = "ready_for_review"
    AWAITING_PR_APPROVAL = "awaiting_pr_approval"
    APPROVED             = "approved"
    MERGED               = "merged"

    # Terminal failure
    FAILED    = "failed"

    # Legacy alias kept for YAML backward compatibility
    COMPLETED = "completed"


class TaskSummary(BaseModel):
    """
    Lightweight mirror of a TaskAggregate's state, owned by GoalAggregate.
    """
    task_id: str
    title: str
    status: TaskStatus
    branch: str
    depends_on: list[str] = Field(default_factory=list)


class GoalAggregate(BaseModel):
    """
    Authoritative aggregate for a software development goal.
    """

    goal_id: str
    name: str
    description: str
    branch: str                                     # goal/<n>
    status: GoalStatus = GoalStatus.PENDING
    tasks: dict[str, TaskSummary] = Field(default_factory=dict)
    state_version: int = 1
    history: list[HistoryEntry] = Field(default_factory=list)
    failure_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # PR-specific fields (set only by open_pr / sync_pr_state)
    pr_number: Optional[int] = None
    pr_status: Optional[str] = None           # "open" | "closed" | "merged"
    pr_checks_passed: bool = False
    pr_approved: bool = False
    pr_head_sha: Optional[str] = None
    pr_html_url: Optional[str] = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        name: str,
        description: str,
        task_summaries: list[TaskSummary],
        goal_id: Optional[str] = None,
    ) -> "GoalAggregate":
        gid = goal_id or f"goal-{uuid4().hex[:12]}"
        return cls(
            goal_id=gid,
            name=name,
            description=description,
            branch=f"goal/{name}",
            tasks={t.task_id: t for t in task_summaries},
        )

    # ------------------------------------------------------------------
    # Domain queries
    # ------------------------------------------------------------------

    def is_terminal(self) -> bool:
        return self.status in (GoalStatus.MERGED, GoalStatus.FAILED)

    def is_pr_phase(self) -> bool:
        return self.status in (
            GoalStatus.READY_FOR_REVIEW,
            GoalStatus.AWAITING_PR_APPROVAL,
            GoalStatus.APPROVED,
        )

    def needs_next_goal_unlock(self) -> bool:
        """True when this goal's success should release dependent goals."""
        return self.status in (GoalStatus.APPROVED, GoalStatus.MERGED)

    def progress(self) -> tuple[int, int]:
        merged = sum(1 for s in self.tasks.values() if s.status == TaskStatus.MERGED)
        return merged, len(self.tasks)

    def pending_task_ids(self) -> list[str]:
        terminal = {TaskStatus.MERGED, TaskStatus.CANCELED}
        return [tid for tid, s in self.tasks.items() if s.status not in terminal]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bump(self, event: str, actor: str, detail: dict[str, Any] | None = None) -> None:
        self.state_version += 1
        self.updated_at = datetime.now(timezone.utc)
        self.history.append(
            HistoryEntry(event=event, actor=actor, detail=detail or {})
        )

    def _assert_not_terminal(self) -> None:
        if self.is_terminal():
            raise ValueError(
                f"Goal {self.goal_id} is already {self.status.value}; "
                "no further mutations are allowed."
            )

    def _get_task(self, task_id: str) -> TaskSummary:
        if task_id not in self.tasks:
            raise KeyError(
                f"Task '{task_id}' is not registered in goal '{self.goal_id}'."
            )
        return self.tasks[task_id]

    def _all_tasks_merged(self) -> bool:
        return bool(self.tasks) and all(
            s.status == TaskStatus.MERGED for s in self.tasks.values()
        )

    # ------------------------------------------------------------------
    # State transitions — task execution phase
    # ------------------------------------------------------------------

    def start(self) -> "GoalAggregate":
        """PENDING → RUNNING. Idempotent if already RUNNING."""
        if self.status == GoalStatus.RUNNING:
            return self
        self._assert_not_terminal()
        self.status = GoalStatus.RUNNING
        self._bump("goal.started", "orchestrator")
        return self

    def record_task_status(self, task_id: str, status: TaskStatus) -> "GoalAggregate":
        """Mirror intermediate task status into TaskSummary (no goal transition)."""
        self._assert_not_terminal()
        task = self._get_task(task_id)
        old = task.status
        task.status = status
        self._bump(
            "goal.task_status_updated",
            "orchestrator",
            {"task_id": task_id, "from": old.value, "to": status.value},
        )
        return self

    def record_task_merged(self, task_id: str) -> "GoalAggregate":
        """
        Mark the task as MERGED into the goal branch.

        When all tasks are merged → READY_FOR_REVIEW (not COMPLETED).
        The PR-driven flow handles the rest.
        """
        self._assert_not_terminal()
        task = self._get_task(task_id)
        task.status = TaskStatus.MERGED
        self._bump("goal.task_merged", "orchestrator", {"task_id": task_id})

        if self._all_tasks_merged():
            merged, total = self.progress()
            self.status = GoalStatus.READY_FOR_REVIEW
            self._bump(
                "goal.ready_for_review",
                "orchestrator",
                {"merged": merged, "total": total},
            )

        return self

    def record_task_canceled(self, task_id: str, reason: str) -> "GoalAggregate":
        """Mark a task as permanently CANCELED, immediately failing the goal."""
        self._assert_not_terminal()
        task = self._get_task(task_id)
        task.status = TaskStatus.CANCELED
        self._bump(
            "goal.task_canceled",
            "orchestrator",
            {"task_id": task_id, "reason": reason},
        )
        self.status = GoalStatus.FAILED
        self.failure_reason = f"Task '{task_id}' permanently canceled: {reason}"
        self._bump(
            "goal.failed",
            "orchestrator",
            {"reason": self.failure_reason},
        )
        return self

    # ------------------------------------------------------------------
    # State transitions — PR review phase
    # ------------------------------------------------------------------

    def open_pr(self, pr_number: int, html_url: str, head_sha: str) -> "GoalAggregate":
        """
        Record that a GitHub PR has been opened for this goal.
        READY_FOR_REVIEW → AWAITING_PR_APPROVAL.

        Only CreateGoalPRUseCase may call this. Agents must NEVER call it.

        Raises ValueError if goal is not READY_FOR_REVIEW, or if PR already set.
        """
        if self.status != GoalStatus.READY_FOR_REVIEW:
            raise ValueError(
                f"Goal '{self.goal_id}' is '{self.status.value}', "
                "not 'ready_for_review'. Cannot open PR in this state."
            )
        if self.pr_number is not None:
            raise ValueError(
                f"Goal '{self.goal_id}' already has PR #{self.pr_number}."
            )

        self.pr_number = pr_number
        self.pr_html_url = html_url
        self.pr_head_sha = head_sha
        self.pr_status = "open"
        self.pr_checks_passed = False
        self.pr_approved = False
        self.status = GoalStatus.AWAITING_PR_APPROVAL
        self._bump(
            "goal.pr_opened",
            "orchestrator",
            {"pr_number": pr_number, "url": html_url, "head_sha": head_sha},
        )
        return self

    def sync_pr_state(
        self,
        *,
        pr_status: str,
        checks_passed: bool,
        approved: bool,
        head_sha: str,
        approval_count: int = 0,
    ) -> "GoalAggregate":
        """
        Update PR-observed fields from a fresh GitHub poll.

        This is a data-update step only — does NOT drive state transitions.
        Call advance_from_pr_state() after this to apply eligible transitions.

        Handles regression: if head_sha changed (new commits pushed), resets
        checks_passed and approved to False and reverts APPROVED → AWAITING.

        Only SyncGoalPRStatusUseCase may call this.
        """
        if self.pr_number is None:
            raise ValueError(
                f"Goal '{self.goal_id}' has no PR. Call open_pr() first."
            )

        old_sha = self.pr_head_sha
        sha_changed = old_sha is not None and head_sha != old_sha

        if sha_changed:
            checks_passed = False
            approved = False
            if self.status == GoalStatus.APPROVED:
                self.status = GoalStatus.AWAITING_PR_APPROVAL
                self._bump(
                    "goal.pr_regression",
                    "reconciler",
                    {
                        "pr_number": self.pr_number,
                        "old_sha": old_sha,
                        "new_sha": head_sha,
                        "reason": "new commits pushed; CI gate reset",
                    },
                )

        self.pr_status = pr_status
        self.pr_checks_passed = checks_passed
        self.pr_approved = approved
        self.pr_head_sha = head_sha
        self._bump(
            "goal.pr_state_synced",
            "reconciler",
            {
                "pr_number": self.pr_number,
                "pr_status": pr_status,
                "checks_passed": checks_passed,
                "approved": approved,
                "approval_count": approval_count,
                "sha_changed": sha_changed,
            },
        )
        return self

    def advance_from_pr_state(self) -> "GoalAggregate":
        """
        Apply eligible PR-driven state transitions after sync_pr_state().

        Transitions:
          AWAITING_PR_APPROVAL + checks_passed + approved → APPROVED
          AWAITING_PR_APPROVAL + pr_status == "closed"   → FAILED
          APPROVED             + pr_status == "merged"   → MERGED
          APPROVED             + pr_status == "closed"   → FAILED

        Idempotent: returns self unchanged if no transition is eligible.
        Only AdvanceGoalFromPRUseCase may call this.
        """
        if self.pr_number is None or self.is_terminal():
            return self

        if self.pr_status == "closed":
            self.status = GoalStatus.FAILED
            self.failure_reason = (
                f"PR #{self.pr_number} was closed without merging."
            )
            self._bump(
                "goal.failed",
                "orchestrator",
                {"reason": self.failure_reason, "pr_number": self.pr_number},
            )
            return self

        if self.pr_status == "merged":
            self.status = GoalStatus.MERGED
            self._bump(
                "goal.merged",
                "orchestrator",
                {"pr_number": self.pr_number, "head_sha": self.pr_head_sha},
            )
            return self

        if (
            self.status == GoalStatus.AWAITING_PR_APPROVAL
            and self.pr_checks_passed
            and self.pr_approved
        ):
            self.status = GoalStatus.APPROVED
            self._bump(
                "goal.approved",
                "orchestrator",
                {"pr_number": self.pr_number, "head_sha": self.pr_head_sha},
            )

        return self

    def record_merged(self, merge_sha: str) -> "GoalAggregate":
        """
        Mark the goal as MERGED after the PR lands (operator-triggered finalize).
        Eligible from APPROVED or AWAITING_PR_APPROVAL only.
        """
        eligible = (GoalStatus.APPROVED, GoalStatus.AWAITING_PR_APPROVAL)
        if self.status not in eligible:
            raise ValueError(
                f"Goal '{self.goal_id}' is '{self.status.value}'. "
                f"Only {[s.value for s in eligible]} goals can be finalized."
            )
        self.status = GoalStatus.MERGED
        self._bump(
            "goal.merged",
            "orchestrator",
            {"merge_sha": merge_sha, "pr_number": self.pr_number},
        )
        return self
