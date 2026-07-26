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
    """A provider's capacity state. `model_id=None` is a PROVIDER-WIDE circuit —
    used for account-level limits (quota, daily quota) that every model on the
    key shares. Per-model circuits carry the concrete model id and cover
    upstream-level limits (an aggregator routes each model to its own pool).

    `opened_at` is the START of the outage, not the last failure: policy rides a
    capacity outage out on automatic waiting and only escalates once the outage
    has lasted longer than the configured wall-clock ceiling.
    """

    runtime: str
    provider_id: str
    model_id: str | None
    failure_count: int
    opened_at: datetime
    retry_at: datetime
    last_failure_kind: str
    safe_message: str
    manual_intervention: bool = False
    limit_scope: str | None = None
    probe_holder: str | None = None
    probe_started_at: datetime | None = None


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

    def count_inflight_attempts(self, runtime: str, provider_id: str, model_id: str | None) -> int:
        """Attempts currently running against a provider, ACROSS ALL PLANS.

        Cross-plan on purpose: an upstream inference pool is shared by every plan
        this orchestrator runs, so a plan-scoped count would let two plans each
        open a full cap's worth and blow straight through the provider's ceiling.

        `model_id=None` counts every model on the provider (an endpoint that
        shares one pool). Counted from ATTEMPTS, not runs: ExecutionRun carries no
        provider binding — the attempt is what records which provider/model ran."""
        ...

    def list_runs(self, plan_id: str) -> list[ExecutionRun]: ...

    def list_attempts(self, plan_id: str) -> list[ExecutionAttempt]: ...

    def add_planning_operation(self, operation: PlanningOperation) -> None: ...

    def update_planning_operation(self, operation: PlanningOperation) -> None: ...

    def find_active_planning_operation(
        self, plan_id: str, purpose: str, target_goal_id: str | None = None
    ) -> PlanningOperation | None: ...

    def list_planning_operations(self, plan_id: str) -> list[PlanningOperation]: ...

    # Circuit accessors take `model_id=None` for a PROVIDER-WIDE circuit. Callers
    # always speak `None`; how a backend stores "no model" is its own business.
    def get_runtime_circuit(
        self, runtime: str, provider_id: str, model_id: str | None
    ) -> RuntimeCircuit | None: ...

    def upsert_runtime_circuit(self, circuit: RuntimeCircuit) -> None: ...

    def try_claim_circuit_probe(
        self,
        runtime: str,
        provider_id: str,
        model_id: str | None,
        *,
        holder: str,
        now: datetime,
        stale_before: datetime,
    ) -> bool:
        """Atomically claim the half-open probe. Returns True for the ONE caller
        that wins; every other concurrent caller gets False and must keep waiting.

        A probe held since before `stale_before` is considered abandoned (its
        holder died) and may be taken. Implementations must make the test and the
        write a single atomic step — a read-then-write would let two runners both
        see it free."""
        ...

    def release_circuit_probe(
        self, runtime: str, provider_id: str, model_id: str | None
    ) -> None: ...

    def clear_runtime_circuit(
        self, runtime: str, provider_id: str, model_id: str | None
    ) -> None: ...
