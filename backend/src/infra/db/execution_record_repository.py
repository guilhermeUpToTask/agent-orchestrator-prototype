"""SQLite adapter for the transactional execution run/attempt ledger."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from src.app.execution_records import (
    ExecutionAttempt,
    ExecutionAttemptStatus,
    PlanningOperation,
    PlanningOperationStatus,
    RuntimeCircuit,
    ExecutionRun,
    ExecutionRunStatus,
)
from src.app.runtime_failures import LimitScope, RuntimeFailure

_RUN_COLUMNS = "id, plan_id, goal_id, task_id, status, started_at, completed_at"
_ATTEMPT_COLUMNS = (
    "id, run_id, plan_id, goal_id, task_id, number, task_attempt, status, "
    "started_at, completed_at, last_liveness_at, timeout_seconds, runtime, "
    "provider_id, model_id, failure_kind, provider_code, retryable, retry_at, "
    "limit_scope, exit_code, safe_message, stdout_tail, stderr_tail"
)
_PLANNING_COLUMNS = (
    "id, plan_id, purpose, target_goal_id, status, created_at, updated_at, "
    "started_at, completed_at, last_liveness_at, model_request_count, "
    "tool_turn_count, runtime, provider_id, model_id, failure_kind, retry_at, "
    "safe_message"
)


def _dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _run(row: object) -> ExecutionRun:
    values = row  # Row supports positional access; keep ORM types out of the port.
    return ExecutionRun(
        id=str(values[0]),  # type: ignore[index]
        plan_id=str(values[1]),  # type: ignore[index]
        goal_id=str(values[2]),  # type: ignore[index]
        task_id=str(values[3]),  # type: ignore[index]
        status=ExecutionRunStatus(str(values[4])),  # type: ignore[index]
        started_at=datetime.fromisoformat(str(values[5])),  # type: ignore[index]
        completed_at=_dt(values[6]),  # type: ignore[index]
    )


def _attempt(row: object) -> ExecutionAttempt:
    values = row
    return ExecutionAttempt(
        id=str(values[0]),  # type: ignore[index]
        run_id=str(values[1]),  # type: ignore[index]
        plan_id=str(values[2]),  # type: ignore[index]
        goal_id=str(values[3]),  # type: ignore[index]
        task_id=str(values[4]),  # type: ignore[index]
        number=int(values[5]),  # type: ignore[index]
        task_attempt=int(values[6]),  # type: ignore[index]
        status=ExecutionAttemptStatus(str(values[7])),  # type: ignore[index]
        started_at=datetime.fromisoformat(str(values[8])),  # type: ignore[index]
        completed_at=_dt(values[9]),  # type: ignore[index]
        last_liveness_at=_dt(values[10]),  # type: ignore[index]
        timeout_seconds=(None if values[11] is None else int(values[11])),  # type: ignore[index]
        runtime=(None if values[12] is None else str(values[12])),  # type: ignore[index]
        provider_id=(None if values[13] is None else str(values[13])),  # type: ignore[index]
        model_id=(None if values[14] is None else str(values[14])),  # type: ignore[index]
        failure_kind=(None if values[15] is None else str(values[15])),  # type: ignore[index]
        provider_code=(None if values[16] is None else str(values[16])),  # type: ignore[index]
        retryable=(None if values[17] is None else bool(values[17])),  # type: ignore[index]
        retry_at=_dt(values[18]),  # type: ignore[index]
        limit_scope=(None if values[19] is None else LimitScope(str(values[19]))),  # type: ignore[index]
        exit_code=(None if values[20] is None else int(values[20])),  # type: ignore[index]
        safe_message=(None if values[21] is None else str(values[21])),  # type: ignore[index]
        stdout_tail=str(values[22] or ""),  # type: ignore[index]
        stderr_tail=str(values[23] or ""),  # type: ignore[index]
    )


def _planning(row: object) -> PlanningOperation:
    values = row
    return PlanningOperation(
        id=str(values[0]),  # type: ignore[index]
        plan_id=str(values[1]),  # type: ignore[index]
        purpose=str(values[2]),  # type: ignore[index]
        target_goal_id=(None if values[3] is None else str(values[3])),  # type: ignore[index]
        status=PlanningOperationStatus(str(values[4])),  # type: ignore[index]
        created_at=datetime.fromisoformat(str(values[5])),  # type: ignore[index]
        updated_at=datetime.fromisoformat(str(values[6])),  # type: ignore[index]
        started_at=_dt(values[7]),  # type: ignore[index]
        completed_at=_dt(values[8]),  # type: ignore[index]
        last_liveness_at=_dt(values[9]),  # type: ignore[index]
        model_request_count=int(values[10]),  # type: ignore[index]
        tool_turn_count=int(values[11]),  # type: ignore[index]
        runtime=(None if values[12] is None else str(values[12])),  # type: ignore[index]
        provider_id=(None if values[13] is None else str(values[13])),  # type: ignore[index]
        model_id=(None if values[14] is None else str(values[14])),  # type: ignore[index]
        failure_kind=(None if values[15] is None else str(values[15])),  # type: ignore[index]
        retry_at=_dt(values[16]),  # type: ignore[index]
        safe_message=(None if values[17] is None else str(values[17])),  # type: ignore[index]
    )


class SqliteExecutionRecordRepository:
    """Bound to the live UoW session; every write shares the Plan/outbox txn."""

    def __init__(self) -> None:
        self._session: Session | None = None

    def bind(self, session: Session) -> None:
        self._session = session

    def unbind(self) -> None:
        self._session = None

    def _bound(self) -> Session:
        if self._session is None:
            raise RuntimeError(
                "SqliteExecutionRecordRepository used outside a UnitOfWork transaction"
            )
        return self._session

    def find_active_run(self, plan_id: str, goal_id: str, task_id: str) -> ExecutionRun | None:
        row = (
            self._bound()
            .execute(
                text(
                    f"SELECT {_RUN_COLUMNS} "
                    "FROM execution_runs "
                    "WHERE plan_id = :plan_id AND goal_id = :goal_id AND task_id = :task_id "
                    "AND status IN ('running', 'retrying') "
                    "ORDER BY started_at DESC LIMIT 1"
                ),
                {"plan_id": plan_id, "goal_id": goal_id, "task_id": task_id},
            )
            .one_or_none()
        )
        return None if row is None else _run(row)

    def add_run(self, run: ExecutionRun) -> None:
        self._bound().execute(
            text(
                "INSERT INTO execution_runs "
                "(id, plan_id, goal_id, task_id, status, started_at, completed_at) "
                "VALUES (:id, :plan_id, :goal_id, :task_id, :status, :started_at, "
                ":completed_at)"
            ),
            {
                "id": run.id,
                "plan_id": run.plan_id,
                "goal_id": run.goal_id,
                "task_id": run.task_id,
                "status": run.status.value,
                "started_at": run.started_at.isoformat(),
                "completed_at": (run.completed_at.isoformat() if run.completed_at else None),
            },
        )

    def next_attempt_number(self, plan_id: str, goal_id: str, task_id: str) -> int:
        value = (
            self._bound()
            .execute(
                text(
                    "SELECT COALESCE(MAX(number), 0) + 1 FROM execution_attempts "
                    "WHERE plan_id = :plan_id AND goal_id = :goal_id AND task_id = :task_id"
                ),
                {"plan_id": plan_id, "goal_id": goal_id, "task_id": task_id},
            )
            .scalar_one()
        )
        return int(value)

    def add_attempt(self, attempt: ExecutionAttempt) -> None:
        self._bound().execute(
            text(
                "INSERT INTO execution_attempts "
                "(id, run_id, plan_id, goal_id, task_id, number, task_attempt, "
                "status, started_at, completed_at, last_liveness_at, timeout_seconds, "
                "runtime, provider_id, model_id) "
                "VALUES (:id, :run_id, :plan_id, :goal_id, :task_id, :number, "
                ":task_attempt, :status, :started_at, :completed_at, "
                ":last_liveness_at, :timeout_seconds, :runtime, :provider_id, :model_id)"
            ),
            {
                "id": attempt.id,
                "run_id": attempt.run_id,
                "plan_id": attempt.plan_id,
                "goal_id": attempt.goal_id,
                "task_id": attempt.task_id,
                "number": attempt.number,
                "task_attempt": attempt.task_attempt,
                "status": attempt.status.value,
                "started_at": attempt.started_at.isoformat(),
                "completed_at": (
                    attempt.completed_at.isoformat() if attempt.completed_at else None
                ),
                "last_liveness_at": (
                    attempt.last_liveness_at.isoformat()
                    if attempt.last_liveness_at
                    else attempt.started_at.isoformat()
                ),
                "timeout_seconds": attempt.timeout_seconds,
                "runtime": attempt.runtime,
                "provider_id": attempt.provider_id,
                "model_id": attempt.model_id,
            },
        )

    def mark_run_running(self, run_id: str) -> None:
        result: CursorResult[Any] = self._bound().execute(  # type: ignore[assignment]
            text(
                "UPDATE execution_runs SET status = 'running', completed_at = NULL "
                "WHERE id = :id AND status IN ('running', 'retrying')"
            ),
            {"id": run_id},
        )
        if result.rowcount != 1:
            raise RuntimeError(f"execution run {run_id!r} is not active")

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
        session = self._bound()
        row = session.execute(
            text("SELECT run_id, status FROM execution_attempts WHERE id = :id"),
            {"id": attempt_id},
        ).one_or_none()
        if row is None:
            raise KeyError(attempt_id)
        run_id, existing_status = str(row[0]), str(row[1])
        if existing_status == ExecutionAttemptStatus.RUNNING.value:
            session.execute(
                text(
                    "UPDATE execution_attempts SET status = :status, "
                    "completed_at = :completed_at, last_liveness_at = :completed_at, "
                    "failure_kind = :failure_kind, provider_code = :provider_code, "
                    "retryable = :retryable, retry_at = :retry_at, "
                    "limit_scope = :limit_scope, exit_code = :exit_code, "
                    "safe_message = :safe_message, stdout_tail = :stdout_tail, "
                    "stderr_tail = :stderr_tail WHERE id = :id"
                ),
                {
                    "id": attempt_id,
                    "status": attempt_status.value,
                    "completed_at": completed_at.isoformat(),
                    "failure_kind": failure.kind.value if failure else None,
                    "provider_code": failure.provider_code if failure else None,
                    "retryable": int(failure.retryable) if failure else None,
                    "retry_at": retry_at.isoformat() if retry_at else None,
                    "limit_scope": (
                        failure.limit_scope.value
                        if failure and failure.limit_scope is not None
                        else None
                    ),
                    "exit_code": failure.exit_code if failure else None,
                    "safe_message": failure.safe_message if failure else None,
                    "stdout_tail": (failure.stdout_tail if failure else stdout_tail[-2_000:]),
                    "stderr_tail": (failure.stderr_tail if failure else stderr_tail[-2_000:]),
                },
            )
        elif existing_status != attempt_status.value:
            raise RuntimeError(f"execution attempt {attempt_id!r} is already {existing_status!r}")

        session.execute(
            text(
                "UPDATE execution_runs SET status = :status, completed_at = :completed_at "
                "WHERE id = :id"
            ),
            {
                "id": run_id,
                "status": run_status.value,
                "completed_at": (
                    None if run_status == ExecutionRunStatus.RETRYING else completed_at.isoformat()
                ),
            },
        )

    def get_run(self, run_id: str) -> ExecutionRun:
        row = (
            self._bound()
            .execute(
                text(f"SELECT {_RUN_COLUMNS} FROM execution_runs WHERE id = :id"),
                {"id": run_id},
            )
            .one_or_none()
        )
        if row is None:
            raise KeyError(run_id)
        return _run(row)

    def get_attempt(self, attempt_id: str) -> ExecutionAttempt:
        row = (
            self._bound()
            .execute(
                text(f"SELECT {_ATTEMPT_COLUMNS} FROM execution_attempts WHERE id = :id"),
                {"id": attempt_id},
            )
            .one_or_none()
        )
        if row is None:
            raise KeyError(attempt_id)
        return _attempt(row)

    def list_open_attempts(self, plan_id: str | None = None) -> list[ExecutionAttempt]:
        where = "WHERE status = 'running'"
        params: dict[str, str] = {}
        if plan_id is not None:
            where += " AND plan_id = :plan_id"
            params["plan_id"] = plan_id
        rows = (
            self._bound()
            .execute(
                text(
                    f"SELECT {_ATTEMPT_COLUMNS} FROM execution_attempts {where} "
                    "ORDER BY started_at, id"
                ),
                params,
            )
            .all()
        )
        return [_attempt(row) for row in rows]

    def list_runs(self, plan_id: str) -> list[ExecutionRun]:
        rows = (
            self._bound()
            .execute(
                text(
                    f"SELECT {_RUN_COLUMNS} FROM execution_runs "
                    "WHERE plan_id = :plan_id ORDER BY started_at, id"
                ),
                {"plan_id": plan_id},
            )
            .all()
        )
        return [_run(row) for row in rows]

    def list_attempts(self, plan_id: str) -> list[ExecutionAttempt]:
        rows = (
            self._bound()
            .execute(
                text(
                    f"SELECT {_ATTEMPT_COLUMNS} FROM execution_attempts "
                    "WHERE plan_id = :plan_id ORDER BY started_at, id"
                ),
                {"plan_id": plan_id},
            )
            .all()
        )
        return [_attempt(row) for row in rows]

    def add_planning_operation(self, operation: PlanningOperation) -> None:
        self._bound().execute(
            text(
                "INSERT INTO planning_operations "
                "(id, plan_id, purpose, target_goal_id, status, created_at, "
                "updated_at, started_at, completed_at, last_liveness_at, "
                "model_request_count, tool_turn_count, runtime, provider_id, "
                "model_id, failure_kind, retry_at, safe_message) VALUES "
                "(:id, :plan_id, :purpose, :target_goal_id, :status, :created_at, "
                ":updated_at, :started_at, :completed_at, :last_liveness_at, "
                ":model_request_count, :tool_turn_count, :runtime, :provider_id, "
                ":model_id, :failure_kind, :retry_at, :safe_message)"
            ),
            self._planning_params(operation),
        )

    def update_planning_operation(self, operation: PlanningOperation) -> None:
        result: CursorResult[Any] = self._bound().execute(  # type: ignore[assignment]
            text(
                "UPDATE planning_operations SET status=:status, updated_at=:updated_at, "
                "started_at=:started_at, completed_at=:completed_at, "
                "last_liveness_at=:last_liveness_at, "
                "model_request_count=:model_request_count, tool_turn_count=:tool_turn_count, "
                "runtime=:runtime, provider_id=:provider_id, model_id=:model_id, "
                "failure_kind=:failure_kind, retry_at=:retry_at, "
                "safe_message=:safe_message WHERE id=:id"
            ),
            self._planning_params(operation),
        )
        if result.rowcount != 1:
            raise KeyError(operation.id)

    @staticmethod
    def _planning_params(operation: PlanningOperation) -> dict[str, Any]:
        return {
            "id": operation.id,
            "plan_id": operation.plan_id,
            "purpose": operation.purpose,
            "target_goal_id": operation.target_goal_id,
            "status": operation.status.value,
            "created_at": operation.created_at.isoformat(),
            "updated_at": operation.updated_at.isoformat(),
            "started_at": operation.started_at.isoformat() if operation.started_at else None,
            "completed_at": (
                operation.completed_at.isoformat() if operation.completed_at else None
            ),
            "last_liveness_at": (
                operation.last_liveness_at.isoformat() if operation.last_liveness_at else None
            ),
            "model_request_count": operation.model_request_count,
            "tool_turn_count": operation.tool_turn_count,
            "runtime": operation.runtime,
            "provider_id": operation.provider_id,
            "model_id": operation.model_id,
            "failure_kind": operation.failure_kind,
            "retry_at": operation.retry_at.isoformat() if operation.retry_at else None,
            "safe_message": operation.safe_message,
        }

    def find_active_planning_operation(
        self, plan_id: str, purpose: str, target_goal_id: str | None = None
    ) -> PlanningOperation | None:
        row = (
            self._bound()
            .execute(
                text(
                    f"SELECT {_PLANNING_COLUMNS} FROM planning_operations "
                    "WHERE plan_id=:plan_id AND purpose=:purpose "
                    "AND target_goal_id IS :target_goal_id "
                    "AND status IN ('queued', 'started', 'waiting_for_user', 'backing_off') "
                    "ORDER BY created_at DESC, id DESC LIMIT 1"
                ),
                {
                    "plan_id": plan_id,
                    "purpose": purpose,
                    "target_goal_id": target_goal_id,
                },
            )
            .one_or_none()
        )
        return None if row is None else _planning(row)

    def list_planning_operations(self, plan_id: str) -> list[PlanningOperation]:
        rows = (
            self._bound()
            .execute(
                text(
                    f"SELECT {_PLANNING_COLUMNS} FROM planning_operations "
                    "WHERE plan_id=:plan_id ORDER BY created_at, id"
                ),
                {"plan_id": plan_id},
            )
            .all()
        )
        return [_planning(row) for row in rows]

    def get_runtime_circuit(
        self, runtime: str, provider_id: str, model_id: str
    ) -> RuntimeCircuit | None:
        row = (
            self._bound()
            .execute(
                text(
                    "SELECT runtime, provider_id, model_id, failure_count, opened_at, "
                    "retry_at, last_failure_kind, safe_message, manual_intervention "
                    "FROM runtime_circuits WHERE runtime=:runtime "
                    "AND provider_id=:provider_id AND model_id=:model_id"
                ),
                {"runtime": runtime, "provider_id": provider_id, "model_id": model_id},
            )
            .one_or_none()
        )
        if row is None:
            return None
        return RuntimeCircuit(
            runtime=str(row[0]),
            provider_id=str(row[1]),
            model_id=str(row[2]),
            failure_count=int(row[3]),
            opened_at=datetime.fromisoformat(str(row[4])),
            retry_at=datetime.fromisoformat(str(row[5])),
            last_failure_kind=str(row[6]),
            safe_message=str(row[7]),
            manual_intervention=bool(row[8]),
        )

    def upsert_runtime_circuit(self, circuit: RuntimeCircuit) -> None:
        self._bound().execute(
            text(
                "INSERT INTO runtime_circuits "
                "(runtime, provider_id, model_id, failure_count, opened_at, retry_at, "
                "last_failure_kind, safe_message, manual_intervention) VALUES "
                "(:runtime, :provider_id, :model_id, :failure_count, :opened_at, "
                ":retry_at, :last_failure_kind, :safe_message, :manual_intervention) "
                "ON CONFLICT(runtime, provider_id, model_id) DO UPDATE SET "
                "failure_count=excluded.failure_count, opened_at=excluded.opened_at, "
                "retry_at=excluded.retry_at, last_failure_kind=excluded.last_failure_kind, "
                "safe_message=excluded.safe_message, "
                "manual_intervention=excluded.manual_intervention"
            ),
            {
                "runtime": circuit.runtime,
                "provider_id": circuit.provider_id,
                "model_id": circuit.model_id,
                "failure_count": circuit.failure_count,
                "opened_at": circuit.opened_at.isoformat(),
                "retry_at": circuit.retry_at.isoformat(),
                "last_failure_kind": circuit.last_failure_kind,
                "safe_message": circuit.safe_message,
                "manual_intervention": int(circuit.manual_intervention),
            },
        )

    def clear_runtime_circuit(self, runtime: str, provider_id: str, model_id: str) -> None:
        self._bound().execute(
            text(
                "DELETE FROM runtime_circuits WHERE runtime=:runtime "
                "AND provider_id=:provider_id AND model_id=:model_id"
            ),
            {"runtime": runtime, "provider_id": provider_id, "model_id": model_id},
        )
