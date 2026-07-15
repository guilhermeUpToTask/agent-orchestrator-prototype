"""Runtime-neutral execution identity and lifecycle records.

These records are operational application state, not domain aggregates and not
telemetry exports.  They give every logical task run and concrete invocation a
stable identity before side effects begin, and make incomplete attempts
discoverable after a worker crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable


class ExecutionRunStatus(str, Enum):
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"


class ExecutionAttemptStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class ExecutionRun:
    id: str
    plan_id: str
    goal_id: str
    task_id: str
    status: ExecutionRunStatus
    started_at: datetime
    completed_at: datetime | None = None


@dataclass(frozen=True)
class ExecutionAttempt:
    id: str
    run_id: str
    plan_id: str
    goal_id: str
    task_id: str
    number: int
    task_attempt: int
    status: ExecutionAttemptStatus
    started_at: datetime
    completed_at: datetime | None = None


@runtime_checkable
class ExecutionRecordRepository(Protocol):
    """Transactional repository bound to the application UnitOfWork."""

    def find_active_run(self, plan_id: str, goal_id: str, task_id: str) -> ExecutionRun | None: ...

    def add_run(self, run: ExecutionRun) -> None: ...

    def next_attempt_number(self, plan_id: str, goal_id: str, task_id: str) -> int: ...

    def add_attempt(self, attempt: ExecutionAttempt) -> None: ...

    def mark_run_running(self, run_id: str) -> None: ...

    def finalize_attempt(
        self,
        attempt_id: str,
        *,
        attempt_status: ExecutionAttemptStatus,
        run_status: ExecutionRunStatus,
        completed_at: datetime,
    ) -> None: ...

    def get_run(self, run_id: str) -> ExecutionRun: ...

    def get_attempt(self, attempt_id: str) -> ExecutionAttempt: ...

    def list_open_attempts(self, plan_id: str | None = None) -> list[ExecutionAttempt]: ...
