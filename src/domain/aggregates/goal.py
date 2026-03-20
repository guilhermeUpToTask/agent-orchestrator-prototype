"""
src/domain/aggregates/goal.py — GoalAggregate.

The Goal is the authoritative unit of a software objective. It owns a
dependency graph of tasks and their collective progress toward a single
outcome: all task branches merged into the goal branch, ready for
finalization into main.

Lifecycle:
    PENDING  ──start()──▶  RUNNING  ──(all merged)──▶  COMPLETED
                                     ──(any canceled)──▶  FAILED

The orchestrator keeps TaskSummary statuses in sync by reacting to task
domain events. GoalAggregate itself never reads from TaskRepository — it
is always told what happened via its transition methods.
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
    PENDING   = "pending"    # created, no tasks dispatched yet
    RUNNING   = "running"    # at least one task in flight
    COMPLETED = "completed"  # all task branches merged into the goal branch
    FAILED    = "failed"     # at least one task permanently canceled


class TaskSummary(BaseModel):
    """
    Lightweight mirror of a TaskAggregate's state, owned by GoalAggregate.

    branch is the full task branch ref: goal/<name>/task/<task_id>.
    The orchestrator updates status here each time it reacts to a task event.
    """

    task_id: str
    title: str
    status: TaskStatus
    branch: str
    depends_on: list[str] = Field(default_factory=list)


class GoalAggregate(BaseModel):
    """
    Authoritative aggregate for a software development goal.

    Invariants:
    - Once COMPLETED or FAILED, no further status mutations are allowed.
    - status transitions to COMPLETED only when every task is MERGED.
    - status transitions to FAILED on the first task CANCELATION.
    - state_version is bumped on every mutation (optimistic concurrency).
    """

    goal_id: str
    name: str
    description: str
    branch: str                                     # goal/<name>
    status: GoalStatus = GoalStatus.PENDING
    tasks: dict[str, TaskSummary] = Field(default_factory=dict)
    state_version: int = 1
    history: list[HistoryEntry] = Field(default_factory=list)
    failure_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

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
        """Return True if no further automatic transitions can occur."""
        return self.status in (GoalStatus.COMPLETED, GoalStatus.FAILED)

    def progress(self) -> tuple[int, int]:
        """Return (merged_count, total_count)."""
        merged = sum(1 for s in self.tasks.values() if s.status == TaskStatus.MERGED)
        return merged, len(self.tasks)

    def pending_task_ids(self) -> list[str]:
        """Return IDs of tasks that have not yet reached a terminal state."""
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
    # State transitions
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
        """
        Mirror a task's current status into its TaskSummary.

        Use this for intermediate status updates (ASSIGNED, IN_PROGRESS, etc.)
        that don't drive goal-level completion logic.
        For the terminal success path, use record_task_merged() instead.
        For the terminal failure path, use record_task_canceled() instead.
        """
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

        If this makes every task MERGED, automatically transitions the goal to
        COMPLETED and records a goal.completed history entry.
        """
        self._assert_not_terminal()
        task = self._get_task(task_id)
        task.status = TaskStatus.MERGED
        self._bump("goal.task_merged", "orchestrator", {"task_id": task_id})

        if self._all_tasks_merged():
            merged, total = self.progress()
            self.status = GoalStatus.COMPLETED
            self._bump(
                "goal.completed",
                "orchestrator",
                {"merged": merged, "total": total},
            )

        return self

    def record_task_canceled(self, task_id: str, reason: str) -> "GoalAggregate":
        """
        Mark the task as permanently CANCELED.

        Immediately fails the goal: dependent tasks can never be dispatched,
        so the objective cannot be achieved in this run.
        """
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
