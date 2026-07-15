"""SQLite Plan repository with CAS, project ownership, and plan leases."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from src.app.ports import Clock
from src.domain.aggregates.planner_orchestrator import Plan
from src.domain.errors.planning_errors import PlanNotFoundError
from src.domain.errors.tasks_errors import StaleVersionError
from src.domain.factories.plan_factory import PlanFactory
from src.infra.db._session import run_in_session

_UPSERT_SQL = text(
    """
    INSERT INTO plans
        (id, project_id, version, status, phase, iteration, data,
         retry_not_before, paused, pause_requested, created_at, updated_at)
    VALUES
        (:id, :project_id, :version, :status, :phase, :iteration, :data,
         :retry_not_before, :paused, :pause_requested, :now, :now)
    ON CONFLICT(id) DO UPDATE SET
        project_id = excluded.project_id,
        version = excluded.version,
        status = excluded.status,
        phase = excluded.phase,
        iteration = excluded.iteration,
        data = excluded.data,
        retry_not_before = excluded.retry_not_before,
        paused = excluded.paused,
        pause_requested = excluded.pause_requested,
        updated_at = excluded.updated_at
    WHERE plans.version < excluded.version
    """
)

_CLAIM_SQL = text(
    """
    UPDATE plans
    SET claimed_by = :worker_id,
        claimed_at = :now_epoch,
        lease_expires_at = :expires_epoch,
        lease_seconds = :lease_seconds
    WHERE id = (
        SELECT id FROM plans
        WHERE status = 'running'
          AND project_id IS NOT NULL
          AND (claimed_by IS NULL OR lease_expires_at < :now_epoch)
          AND (retry_not_before IS NULL OR retry_not_before < :now_epoch)
          AND paused = 0
          AND pause_requested = 0
        ORDER BY updated_at
        LIMIT 1
    )
    RETURNING data
    """
)

_HEARTBEAT_SQL = text(
    """
    UPDATE plans
    SET lease_expires_at = :now_epoch + lease_seconds
    WHERE id = :plan_id AND claimed_by = :worker_id
    """
)

_RELEASE_SQL = text(
    """
    UPDATE plans
    SET claimed_by = NULL, claimed_at = NULL,
        lease_expires_at = NULL, lease_seconds = NULL
    WHERE id = :plan_id AND claimed_by = :worker_id
    """
)


class SqlitePlanRepository:
    def __init__(self, session_factory: sessionmaker[Session], clock: Clock) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._session: Session | None = None

    def bind(self, session: Session) -> None:
        self._session = session

    def unbind(self) -> None:
        self._session = None

    def _bound(self) -> Session:
        if self._session is None:
            raise RuntimeError(
                "SqlitePlanRepository used outside a UnitOfWork transaction"
            )
        return self._session

    def get(self, plan_id: str) -> Plan:
        row = self._bound().execute(
            text("SELECT data FROM plans WHERE id = :id"), {"id": plan_id}
        ).one_or_none()
        if row is None:
            raise PlanNotFoundError(plan_id)
        return PlanFactory.reconstruct(json.loads(row[0]))

    def save(self, plan: Plan) -> None:
        session = self._bound()
        result: CursorResult[Any] = session.execute(  # type: ignore[assignment]
            _UPSERT_SQL,
            {
                "id": plan.id,
                "project_id": plan.project_id,
                "version": plan.version,
                "status": plan.status.value,
                "phase": plan.phase.value,
                "iteration": plan.iteration,
                "data": plan.model_dump_json(),
                "retry_not_before": (
                    int(plan.planning_retry_not_before.timestamp())
                    if plan.planning_retry_not_before is not None
                    else None
                ),
                "paused": int(plan.paused),
                "pause_requested": int(plan.pause_requested),
                "now": self._clock.now().isoformat(),
            },
        )
        if result.rowcount == 0:
            stored = session.execute(
                text("SELECT version FROM plans WHERE id = :id"), {"id": plan.id}
            ).scalar_one()
            raise StaleVersionError(plan.id, plan.version, int(stored))

    def find_by_project_id(self, project_id: str) -> str | None:
        row = self._bound().execute(
            text("SELECT id FROM plans WHERE project_id = :project_id"),
            {"project_id": project_id},
        ).one_or_none()
        return None if row is None else str(row[0])

    def find_by_request_id(self, request_id: str) -> str | None:
        row = self._bound().execute(
            text("SELECT plan_id FROM plan_requests WHERE request_id = :rid"),
            {"rid": request_id},
        ).one_or_none()
        return None if row is None else str(row[0])

    def bind_request_id(self, request_id: str, plan_id: str) -> None:
        self._bound().execute(
            text(
                "INSERT OR IGNORE INTO plan_requests (request_id, plan_id)"
                " VALUES (:rid, :pid)"
            ),
            {"rid": request_id, "pid": plan_id},
        )

    def claim_one_unit(self, worker_id: str, lease_seconds: int) -> Plan | None:
        now_epoch = int(self._clock.now().timestamp())

        def claim(session: Session) -> str | None:
            row = session.execute(
                _CLAIM_SQL,
                {
                    "worker_id": worker_id,
                    "now_epoch": now_epoch,
                    "expires_epoch": now_epoch + lease_seconds,
                    "lease_seconds": lease_seconds,
                },
            ).one_or_none()
            return None if row is None else str(row[0])

        data = run_in_session(self._session_factory, claim)
        return None if data is None else PlanFactory.reconstruct(json.loads(data))

    def heartbeat(self, plan_id: str, worker_id: str) -> None:
        now_epoch = int(self._clock.now().timestamp())
        run_in_session(
            self._session_factory,
            lambda session: session.execute(
                _HEARTBEAT_SQL,
                {"plan_id": plan_id, "worker_id": worker_id, "now_epoch": now_epoch},
            ),
        )

    def release(self, plan_id: str, worker_id: str) -> None:
        run_in_session(
            self._session_factory,
            lambda session: session.execute(
                _RELEASE_SQL, {"plan_id": plan_id, "worker_id": worker_id}
            ),
        )

    def list_summaries(self) -> list[dict[str, object]]:
        with self._session_factory() as session:
            rows = session.execute(
                text(
                    "SELECT id, project_id, status, phase, iteration, version, "
                    "claimed_by, updated_at, paused, pause_requested "
                    "FROM plans ORDER BY updated_at DESC"
                )
            ).all()
        return [
            {
                "id": row[0],
                "project_id": row[1],
                "status": row[2],
                "phase": row[3],
                "iteration": row[4],
                "version": row[5],
                "claimed_by": row[6],
                "updated_at": row[7],
                "paused": bool(row[8]),
                "pause_requested": bool(row[9]),
            }
            for row in rows
        ]
