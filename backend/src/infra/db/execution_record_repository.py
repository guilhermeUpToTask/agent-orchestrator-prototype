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
    ExecutionRun,
    ExecutionRunStatus,
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
                    "SELECT id, plan_id, goal_id, task_id, status, started_at, completed_at "
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
                "status, started_at, completed_at) "
                "VALUES (:id, :run_id, :plan_id, :goal_id, :task_id, :number, "
                ":task_attempt, :status, :started_at, :completed_at)"
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
                    "completed_at = :completed_at WHERE id = :id"
                ),
                {
                    "id": attempt_id,
                    "status": attempt_status.value,
                    "completed_at": completed_at.isoformat(),
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
                text(
                    "SELECT id, plan_id, goal_id, task_id, status, started_at, completed_at "
                    "FROM execution_runs WHERE id = :id"
                ),
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
                text(
                    "SELECT id, run_id, plan_id, goal_id, task_id, number, task_attempt, "
                    "status, started_at, completed_at FROM execution_attempts WHERE id = :id"
                ),
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
                    "SELECT id, run_id, plan_id, goal_id, task_id, number, task_attempt, "
                    f"status, started_at, completed_at FROM execution_attempts {where} "
                    "ORDER BY started_at, id"
                ),
                params,
            )
            .all()
        )
        return [_attempt(row) for row in rows]
