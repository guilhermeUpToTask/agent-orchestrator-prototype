"""
src/domain/aggregates/task.py — TaskAggregate.

The aggregate is the single authoritative source of truth for task state.
All invariant enforcement, state-transition logic, and domain queries live
here. No layer outside the domain ever mutates task fields directly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from src.domain.errors import InvalidStatusTransitionError
from src.domain.value_objects.status import TaskStatus
from src.domain.value_objects.task import (
    AgentSelector,
    Assignment,
    ExecutionSpec,
    HistoryEntry,
    RetryPolicy,
    TaskResult,
)

if TYPE_CHECKING:
    from src.domain.entities.agent import AgentProps

# Upper bound on liveness reclaims (lease-expiry / dead-agent requeues). Unlike
# the genuine-failure retry budget, reclaims do not mean the task is broken — a
# generous cap just prevents an unrunnable task from churning forever.
MAX_RECLAIMS = 10


class TaskAggregate(BaseModel):
    """
    Authoritative entity for a unit of work.

    Transitions: assign → start → complete/fail → requeue → assign …
    Each transition validates source status, mutates state, bumps
    state_version (optimistic concurrency), and appends a history entry.
    """

    task_id: str
    feature_id: str
    title: str
    description: str
    agent_selector: AgentSelector
    execution: ExecutionSpec
    status: TaskStatus = TaskStatus.CREATED
    assignment: Optional[Assignment] = None
    state_version: int = 1
    history: list[HistoryEntry] = Field(default_factory=list)
    result: Optional[TaskResult] = None
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_error: Optional[str] = None
    depends_on: list[str] = Field(default_factory=list)
    # Count of liveness reclaims (lease-expiry / dead-agent requeues). Tracked
    # separately from retry_policy so infra hiccups don't consume the
    # genuine-failure budget.
    reclaim_count: int = 0
    # Set when assignment finds no eligible agent for required_capability;
    # cleared on a successful assign(). Surfaced to the UI as a sticky warning.
    unassignable_reason: Optional[str] = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        title: str,
        description: str,
        execution: ExecutionSpec,
        agent_selector: AgentSelector,
        feature_id: Optional[str] = None,
        depends_on: Optional[list[str]] = None,
        max_retries: int = 2,
        task_id: Optional[str] = None,
    ) -> "TaskAggregate":
        return cls(
            task_id=task_id or f"task-{uuid4().hex[:12]}",
            feature_id=feature_id or f"feat-{uuid4().hex[:8]}",
            title=title,
            description=description,
            agent_selector=agent_selector,
            execution=execution,
            depends_on=depends_on or [],
            retry_policy=RetryPolicy(max_retries=max_retries),
        )

    # ------------------------------------------------------------------
    # Domain queries — read-only decisions about this task's state
    # ------------------------------------------------------------------

    def is_assignable(self) -> bool:
        """Return True if this task can currently receive an agent assignment."""
        return self.status in TaskStatus.assignable()

    def is_unblocked(self, completed_task_ids: set[str]) -> bool:
        """Return True if all declared dependencies have succeeded."""
        if not self.depends_on:
            return True
        return all(dep in completed_task_ids for dep in self.depends_on)

    def is_ready_for_dispatch(self, succeeded_ids: set[str]) -> bool:
        """
        Return True if this task is waiting for assignment AND all its
        dependencies have already succeeded.
        """
        return self.status == TaskStatus.CREATED and self.is_unblocked(succeeded_ids)

    def needs_retry(self) -> bool:
        """Return True if this failed task should be automatically requeued."""
        return self.status == TaskStatus.FAILED and self.retry_policy.can_retry()

    def needs_cancel(self) -> bool:
        """Return True if this failed task should be permanently cancelled."""
        return self.status == TaskStatus.FAILED and not self.retry_policy.can_retry()

    def is_stuck_pending(self, threshold_seconds: int) -> bool:
        """
        Return True if the task has been waiting in CREATED or REQUEUED
        for longer than threshold_seconds with no assignment.
        """
        if self.status not in TaskStatus.assignable():
            return False
        age = (datetime.now(timezone.utc) - self.updated_at).total_seconds()
        return age >= threshold_seconds

    def is_lease_expired(self, lease_active: bool) -> bool:
        """
        Return True if an ASSIGNED or IN_PROGRESS task no longer holds an
        active lease — meaning the worker timed out or crashed.
        """
        if self.status not in TaskStatus.active():
            return False
        return not lease_active

    def is_assigned_to_dead_agent(self, agent: Optional["AgentProps"]) -> bool:
        """
        Return True if the assigned agent has missed its heartbeat threshold.
        """
        if self.status != TaskStatus.ASSIGNED or self.assignment is None:
            return False
        if agent is None:
            return False
        return not agent.is_alive()

    def can_retry(self) -> bool:
        """Return True if the retry policy still has budget remaining."""
        return self.retry_policy.can_retry()

    def can_reclaim(self) -> bool:
        """Return True if this task may still be reclaimed (requeued) after a
        lease expiry / dead agent — i.e. it hasn't churned past MAX_RECLAIMS."""
        return self.reclaim_count < MAX_RECLAIMS

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_status(self, *allowed: TaskStatus) -> None:
        if self.status not in allowed:
            raise InvalidStatusTransitionError(
                task_id=self.task_id,
                current=self.status.value,
                allowed=[s.value for s in allowed],
            )

    def _bump(self, event: str, actor: str, detail: dict[str, Any] | None = None) -> None:
        self.state_version += 1
        self.updated_at = datetime.now(timezone.utc)
        self.history.append(
            HistoryEntry(event=event, actor=actor, detail=detail or {})
        )

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def assign(self, assignment: Assignment) -> "TaskAggregate":
        self._assert_status(*TaskStatus.assignable())
        self.assignment = assignment
        self.status = TaskStatus.ASSIGNED
        self.unassignable_reason = None  # cleared: a capable agent was found
        self._bump(
            "task.assigned",
            assignment.agent_id,
            {"lease_seconds": assignment.lease_seconds},
        )
        return self

    def mark_unassignable(self, reason: str) -> "TaskAggregate":
        """Record that no eligible agent exists for this task's capability.

        Status is unchanged (the task stays assignable and the reconciler keeps
        retrying); this only annotates *why* it is stuck so the UI can surface
        it. Returns self unchanged if the reason already matches, so callers can
        dedupe the accompanying ``task.unassignable`` event.
        """
        if self.unassignable_reason == reason:
            return self
        self.unassignable_reason = reason
        self._bump("task.unassignable", "scheduler", {"reason": reason})
        return self

    def start(self) -> "TaskAggregate":
        self._assert_status(TaskStatus.ASSIGNED)
        self.status = TaskStatus.IN_PROGRESS
        self._bump(
            "task.started",
            self.assignment.agent_id if self.assignment else "unknown",
        )
        return self

    def complete(self, result: TaskResult) -> "TaskAggregate":
        self._assert_status(TaskStatus.IN_PROGRESS)
        self.result = result
        self.status = TaskStatus.SUCCEEDED
        self._bump(
            "task.completed",
            self.assignment.agent_id if self.assignment else "unknown",
            {"commit_sha": result.commit_sha, "branch": result.branch},
        )
        return self

    def fail(self, reason: str) -> "TaskAggregate":
        self._assert_status(TaskStatus.IN_PROGRESS, TaskStatus.ASSIGNED)
        self.status = TaskStatus.FAILED
        self.last_error = reason
        self._bump(
            "task.failed",
            self.assignment.agent_id if self.assignment else "system",
            {"reason": reason},
        )
        return self

    def requeue(self) -> "TaskAggregate":
        """Automatic retry — increments the attempt counter."""
        self._assert_status(TaskStatus.FAILED)
        self.retry_policy.increment(self.task_id)
        self.assignment = None
        self.status = TaskStatus.REQUEUED
        self._bump(
            "task.requeued",
            "reconciler",
            {"attempt": self.retry_policy.attempt},
        )
        return self

    def reclaim(self, reason: str) -> "TaskAggregate":
        """Liveness reclaim — ASSIGNED/IN_PROGRESS → REQUEUED after a lease
        expiry or dead agent. The worker is presumed gone, so the task returns
        to the queue. Increments reclaim_count (its own budget) and deliberately
        does NOT touch retry_policy — a slow/restarted worker is not a task failure.
        """
        self._assert_status(TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS)
        self.assignment = None
        self.reclaim_count += 1
        self.status = TaskStatus.REQUEUED
        self._bump(
            "task.reclaimed",
            "reconciler",
            {"reason": reason, "reclaim_count": self.reclaim_count},
        )
        return self

    def force_requeue(self, actor: str = "operator") -> "TaskAggregate":
        """
        Operator override — resets to REQUEUED without incrementing the
        retry counter.  Blocked only for MERGED (already promoted to VCS).
        """
        if self.status == TaskStatus.MERGED:
            raise ValueError(f"Task {self.task_id} is MERGED and cannot be requeued.")
        previous = self.status
        self.assignment = None
        self.unassignable_reason = None  # clear stale badge; reassignment re-checks
        self.status = TaskStatus.REQUEUED
        self._bump("task.force_requeued", actor, {"previous_status": previous.value})
        return self

    def cancel(self, reason: str = "") -> "TaskAggregate":
        self._assert_status(
            TaskStatus.CREATED,
            TaskStatus.REQUEUED,
            TaskStatus.ASSIGNED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.FAILED,
        )
        self.assignment = None
        self.status = TaskStatus.CANCELED
        self._bump("task.canceled", "system", {"reason": reason})
        return self

    def mark_merged(self) -> "TaskAggregate":
        self._assert_status(TaskStatus.SUCCEEDED)
        self.status = TaskStatus.MERGED
        self._bump("task.merged", "system")
        return self
