"""SQLite adapter for the acceptance-run ledger (migration 0018).

Bound to the live UoW session, exactly like `goal_promotion_repository.py`:
sharing the transaction keeps this off the write-lock critical path, because
SQLite in WAL mode admits one writer and a separate connection would become a
second contender against the hottest write in the system. `contract_repair`
learned that the expensive way — see the Phase 2 deadlock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import Row, text
from sqlalchemy.orm import Session

from praxis_orchestrator.app.acceptance_records import AcceptanceRun
from praxis_orchestrator.app.environment_port import AcceptanceOutcome, AcceptanceTrigger

_COLUMNS = (
    "id, plan_id, cycle_id, goal_id, trigger, ref, outcome, summary, "
    "detail, duration_seconds, created_at"
)


def _run(row: Row[Any]) -> AcceptanceRun:
    return AcceptanceRun(
        id=row.id,
        plan_id=row.plan_id,
        cycle_id=row.cycle_id,
        goal_id=row.goal_id,
        trigger=cast(AcceptanceTrigger, row.trigger),
        ref=row.ref,
        outcome=cast(AcceptanceOutcome, row.outcome),
        summary=row.summary,
        detail=row.detail,
        duration_seconds=row.duration_seconds,
        created_at=datetime.fromisoformat(row.created_at),
    )


class SqliteAcceptanceRunRepository:
    def __init__(self) -> None:
        self._session: Session | None = None

    def bind(self, session: Session) -> None:
        self._session = session

    def unbind(self) -> None:
        self._session = None

    def _bound(self) -> Session:
        if self._session is None:
            raise RuntimeError(
                "SqliteAcceptanceRunRepository used outside a UnitOfWork transaction"
            )
        return self._session

    def add(self, run: AcceptanceRun) -> None:
        self._bound().execute(
            text(
                f"INSERT INTO acceptance_runs ({_COLUMNS}) VALUES "
                "(:id, :plan_id, :cycle_id, :goal_id, :trigger, :ref, :outcome, "
                ":summary, :detail, :duration_seconds, :created_at)"
            ),
            {
                "id": run.id,
                "plan_id": run.plan_id,
                "cycle_id": run.cycle_id,
                "goal_id": run.goal_id,
                "trigger": run.trigger,
                "ref": run.ref,
                "outcome": run.outcome,
                "summary": run.summary,
                "detail": run.detail,
                "duration_seconds": run.duration_seconds,
                "created_at": run.created_at.isoformat(),
            },
        )

    def list_for_cycle(self, plan_id: str, cycle_id: str) -> list[AcceptanceRun]:
        rows = (
            self._bound()
            .execute(
                text(
                    f"SELECT {_COLUMNS} FROM acceptance_runs "
                    "WHERE plan_id = :plan_id AND cycle_id = :cycle_id "
                    "ORDER BY created_at, id"
                ),
                {"plan_id": plan_id, "cycle_id": cycle_id},
            )
            .all()
        )
        return [_run(row) for row in rows]

    def latest_for_cycle(self, plan_id: str, cycle_id: str) -> AcceptanceRun | None:
        runs = self.list_for_cycle(plan_id, cycle_id)
        return runs[-1] if runs else None
