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

from src.app.runtime_failures import LimitScope, RuntimeFailure


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
    last_liveness_at: datetime | None = None
    timeout_seconds: int | None = None
    runtime: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    failure_kind: str | None = None
    provider_code: str | None = None
    retryable: bool | None = None
    retry_at: datetime | None = None
    limit_scope: LimitScope | None = None
    exit_code: int | None = None
    safe_message: str | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""


class PlanningOperationStatus(str, Enum):
    QUEUED = "queued"
    STARTED = "started"
    WAITING_FOR_USER = "waiting_for_user"
    COMMITTED = "committed"
    FAILED = "failed"
    BACKING_OFF = "backing_off"


@dataclass(frozen=True)
class PlanningOperation:
    id: str
    plan_id: str
    purpose: str
    status: PlanningOperationStatus
    created_at: datetime
    updated_at: datetime
    target_goal_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_liveness_at: datetime | None = None
    model_request_count: int = 0
    tool_turn_count: int = 0
    runtime: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    failure_kind: str | None = None
    retry_at: datetime | None = None
    safe_message: str | None = None


@dataclass(frozen=True)
class RuntimeCircuit:
    runtime: str
    provider_id: str
    model_id: str
    failure_count: int
    opened_at: datetime
    retry_at: datetime
    last_failure_kind: str
    safe_message: str
    manual_intervention: bool = False


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
        failure: RuntimeFailure | None = None,
        retry_at: datetime | None = None,
        stdout_tail: str = "",
        stderr_tail: str = "",
    ) -> None: ...

    def get_run(self, run_id: str) -> ExecutionRun: ...

    def get_attempt(self, attempt_id: str) -> ExecutionAttempt: ...

    def list_open_attempts(self, plan_id: str | None = None) -> list[ExecutionAttempt]: ...

    def list_runs(self, plan_id: str) -> list[ExecutionRun]: ...

    def list_attempts(self, plan_id: str) -> list[ExecutionAttempt]: ...

    def add_planning_operation(self, operation: PlanningOperation) -> None: ...

    def update_planning_operation(self, operation: PlanningOperation) -> None: ...

    def find_active_planning_operation(
        self, plan_id: str, purpose: str, target_goal_id: str | None = None
    ) -> PlanningOperation | None: ...

    def list_planning_operations(self, plan_id: str) -> list[PlanningOperation]: ...

    def get_runtime_circuit(
        self, runtime: str, provider_id: str, model_id: str
    ) -> RuntimeCircuit | None: ...

    def upsert_runtime_circuit(self, circuit: RuntimeCircuit) -> None: ...

    def clear_runtime_circuit(self, runtime: str, provider_id: str, model_id: str) -> None: ...
