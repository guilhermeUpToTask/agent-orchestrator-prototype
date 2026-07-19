"""In-memory transactional fake for execution run/attempt records."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from src.app.execution_records import (
    ExecutionAttempt,
    ExecutionAttemptStatus,
    PlanningOperation,
    PlanningOperationStatus,
    RuntimeCircuit,
    ExecutionRun,
    ExecutionRunStatus,
)
from src.app.runtime_failures import RuntimeFailure


class InMemoryExecutionRecordRepository:
    """Copy-on-enter transaction semantics matching the bound SQLite adapter."""

    def __init__(self) -> None:
        self._runs: dict[str, ExecutionRun] = {}
        self._attempts: dict[str, ExecutionAttempt] = {}
        self._planning: dict[str, PlanningOperation] = {}
        self._circuits: dict[tuple[str, str, str], RuntimeCircuit] = {}
        self._tx_runs: dict[str, ExecutionRun] | None = None
        self._tx_attempts: dict[str, ExecutionAttempt] | None = None
        self._tx_planning: dict[str, PlanningOperation] | None = None
        self._tx_circuits: dict[tuple[str, str, str], RuntimeCircuit] | None = None

    def _begin(self) -> None:
        if self._tx_runs is not None:
            raise RuntimeError("execution record transactions cannot be nested")
        self._tx_runs = dict(self._runs)
        self._tx_attempts = dict(self._attempts)
        self._tx_planning = dict(self._planning)
        self._tx_circuits = dict(self._circuits)

    def _commit(self) -> None:
        assert (
            self._tx_runs is not None
            and self._tx_attempts is not None
            and self._tx_planning is not None
            and self._tx_circuits is not None
        )
        self._runs = self._tx_runs
        self._attempts = self._tx_attempts
        self._planning = self._tx_planning
        self._circuits = self._tx_circuits
        self._tx_runs = None
        self._tx_attempts = None
        self._tx_planning = None
        self._tx_circuits = None

    def _rollback(self) -> None:
        self._tx_runs = None
        self._tx_attempts = None
        self._tx_planning = None
        self._tx_circuits = None

    def _bound(
        self,
    ) -> tuple[dict[str, ExecutionRun], dict[str, ExecutionAttempt]]:
        if self._tx_runs is None or self._tx_attempts is None:
            raise RuntimeError(
                "InMemoryExecutionRecordRepository used outside a UnitOfWork transaction"
            )
        return self._tx_runs, self._tx_attempts

    def find_active_run(self, plan_id: str, goal_id: str, task_id: str) -> ExecutionRun | None:
        runs, _ = self._bound()
        active = [
            run
            for run in runs.values()
            if run.plan_id == plan_id
            and run.goal_id == goal_id
            and run.task_id == task_id
            and run.status in (ExecutionRunStatus.RUNNING, ExecutionRunStatus.RETRYING)
        ]
        if len(active) > 1:
            raise RuntimeError("multiple active execution runs for one task")
        return active[0] if active else None

    def add_run(self, run: ExecutionRun) -> None:
        runs, _ = self._bound()
        if run.id in runs:
            raise RuntimeError(f"duplicate execution run {run.id!r}")
        if self.find_active_run(run.plan_id, run.goal_id, run.task_id) is not None:
            raise RuntimeError("an active execution run already exists for this task")
        runs[run.id] = run

    def next_attempt_number(self, plan_id: str, goal_id: str, task_id: str) -> int:
        _, attempts = self._bound()
        numbers = [
            attempt.number
            for attempt in attempts.values()
            if attempt.plan_id == plan_id
            and attempt.goal_id == goal_id
            and attempt.task_id == task_id
        ]
        return max(numbers, default=0) + 1

    def add_attempt(self, attempt: ExecutionAttempt) -> None:
        runs, attempts = self._bound()
        if attempt.id in attempts:
            raise RuntimeError(f"duplicate execution attempt {attempt.id!r}")
        if attempt.run_id not in runs:
            raise KeyError(attempt.run_id)
        if any(
            existing.plan_id == attempt.plan_id
            and existing.goal_id == attempt.goal_id
            and existing.task_id == attempt.task_id
            and existing.number == attempt.number
            for existing in attempts.values()
        ):
            raise RuntimeError("duplicate task-lifetime execution attempt number")
        attempts[attempt.id] = attempt

    def mark_run_running(self, run_id: str) -> None:
        runs, _ = self._bound()
        run = runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status not in (ExecutionRunStatus.RUNNING, ExecutionRunStatus.RETRYING):
            raise RuntimeError(f"execution run {run_id!r} is not active")
        runs[run_id] = replace(run, status=ExecutionRunStatus.RUNNING, completed_at=None)

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
    ) -> None:
        runs, attempts = self._bound()
        attempt = attempts.get(attempt_id)
        if attempt is None:
            raise KeyError(attempt_id)
        if attempt.status == ExecutionAttemptStatus.RUNNING:
            attempts[attempt_id] = replace(
                attempt,
                status=attempt_status,
                completed_at=completed_at,
                last_liveness_at=completed_at,
                failure_kind=(failure.kind.value if failure else None),
                provider_code=(failure.provider_code if failure else None),
                retryable=(failure.retryable if failure else None),
                retry_at=retry_at,
                limit_scope=(failure.limit_scope if failure else None),
                exit_code=(failure.exit_code if failure else None),
                safe_message=(failure.safe_message if failure else None),
                stdout_tail=(failure.stdout_tail if failure else stdout_tail[-2_000:]),
                stderr_tail=(failure.stderr_tail if failure else stderr_tail[-2_000:]),
            )
        elif attempt.status != attempt_status:
            raise RuntimeError(
                f"execution attempt {attempt_id!r} is already {attempt.status.value!r}"
            )
        run = runs[attempt.run_id]
        runs[run.id] = replace(
            run,
            status=run_status,
            completed_at=(None if run_status == ExecutionRunStatus.RETRYING else completed_at),
        )

    def get_run(self, run_id: str) -> ExecutionRun:
        runs = self._runs if self._tx_runs is None else self._tx_runs
        if run_id not in runs:
            raise KeyError(run_id)
        return runs[run_id]

    def get_attempt(self, attempt_id: str) -> ExecutionAttempt:
        attempts = self._attempts if self._tx_attempts is None else self._tx_attempts
        if attempt_id not in attempts:
            raise KeyError(attempt_id)
        return attempts[attempt_id]

    def list_open_attempts(self, plan_id: str | None = None) -> list[ExecutionAttempt]:
        attempts = self._attempts if self._tx_attempts is None else self._tx_attempts
        return sorted(
            (
                attempt
                for attempt in attempts.values()
                if attempt.status == ExecutionAttemptStatus.RUNNING
                and (plan_id is None or attempt.plan_id == plan_id)
            ),
            key=lambda attempt: (attempt.started_at, attempt.id),
        )

    def list_runs(self, plan_id: str) -> list[ExecutionRun]:
        runs = self._runs if self._tx_runs is None else self._tx_runs
        return sorted(
            (run for run in runs.values() if run.plan_id == plan_id),
            key=lambda run: (run.started_at, run.id),
        )

    def list_attempts(self, plan_id: str) -> list[ExecutionAttempt]:
        attempts = self._attempts if self._tx_attempts is None else self._tx_attempts
        return sorted(
            (attempt for attempt in attempts.values() if attempt.plan_id == plan_id),
            key=lambda attempt: (attempt.started_at, attempt.id),
        )

    def _planning_bound(self) -> dict[str, PlanningOperation]:
        if self._tx_planning is None:
            raise RuntimeError(
                "InMemoryExecutionRecordRepository used outside a UnitOfWork transaction"
            )
        return self._tx_planning

    def _circuits_bound(self) -> dict[tuple[str, str, str], RuntimeCircuit]:
        if self._tx_circuits is None:
            raise RuntimeError(
                "InMemoryExecutionRecordRepository used outside a UnitOfWork transaction"
            )
        return self._tx_circuits

    def add_planning_operation(self, operation: PlanningOperation) -> None:
        operations = self._planning_bound()
        if operation.id in operations:
            raise RuntimeError(f"duplicate planning operation {operation.id!r}")
        operations[operation.id] = operation

    def update_planning_operation(self, operation: PlanningOperation) -> None:
        operations = self._planning_bound()
        if operation.id not in operations:
            raise KeyError(operation.id)
        operations[operation.id] = operation

    def find_active_planning_operation(
        self, plan_id: str, purpose: str, target_goal_id: str | None = None
    ) -> PlanningOperation | None:
        operations = self._planning if self._tx_planning is None else self._tx_planning
        active = [
            operation
            for operation in operations.values()
            if operation.plan_id == plan_id
            and operation.purpose == purpose
            and operation.target_goal_id == target_goal_id
            and operation.status
            in {
                PlanningOperationStatus.QUEUED,
                PlanningOperationStatus.STARTED,
                PlanningOperationStatus.WAITING_FOR_USER,
                PlanningOperationStatus.BACKING_OFF,
            }
        ]
        return max(active, key=lambda operation: (operation.created_at, operation.id), default=None)

    def list_planning_operations(self, plan_id: str) -> list[PlanningOperation]:
        operations = self._planning if self._tx_planning is None else self._tx_planning
        return sorted(
            (operation for operation in operations.values() if operation.plan_id == plan_id),
            key=lambda operation: (operation.created_at, operation.id),
        )

    def get_runtime_circuit(
        self, runtime: str, provider_id: str, model_id: str
    ) -> RuntimeCircuit | None:
        circuits = self._circuits if self._tx_circuits is None else self._tx_circuits
        return circuits.get((runtime, provider_id, model_id))

    def upsert_runtime_circuit(self, circuit: RuntimeCircuit) -> None:
        self._circuits_bound()[(circuit.runtime, circuit.provider_id, circuit.model_id)] = circuit

    def clear_runtime_circuit(self, runtime: str, provider_id: str, model_id: str) -> None:
        self._circuits_bound().pop((runtime, provider_id, model_id), None)
