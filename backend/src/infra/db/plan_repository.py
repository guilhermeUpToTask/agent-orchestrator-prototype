"""
src/infra/db/plan_repository.py — SqlitePlanRepository (the PlanRepository port).

Contracts mirrored EXACTLY from the in-memory fake the application suite runs on:

- Detached aggregates: get() parses fresh JSON -> PlanFactory.reconstruct; no ORM
  mapping, no instance caching — a returned Plan can never alias stored state.
- Version CAS: use cases bump_version() BEFORE save(); the store rejects when
  stored.version >= incoming.version. Implemented as one upsert whose UPDATE arm
  only fires when ``plans.version < excluded.version``; rowcount 0 -> reload the
  stored version -> StaleVersionError. save() never touches the lease columns.
- Lease: claim_one_unit is a single atomic UPDATE..RETURNING over the driver-model
  claim predicate (phase ∈ ARCHITECTURE/ENRICHING/RUNNING, lease NULL or expired).
  Lease times are integer epochs from the injected Clock, so FakeClock.advance()
  drives expiry deterministically in the truth tests.

Transaction binding: get/save/find_by_request_id/bind_request_id run on the
UnitOfWork's live session (bound via bind()/unbind() per with-block). The lease
trio (claim/heartbeat/release) is called by the worker loop OUTSIDE any UoW
block, so each runs on its own short session via run_in_session.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from src.app.ports import Clock
from src.domain.aggregates.planner_orchestrator import (
    Plan,
    WORKER_CLAIMABLE_PHASES,
)
from src.domain.errors.planning_errors import PlanNotFoundError
from src.domain.errors.tasks_errors import StaleVersionError
from src.domain.factories.plan_factory import PlanFactory
from src.infra.db._session import run_in_session

_CLAIMABLE_PHASES = tuple(sorted(p.value for p in WORKER_CLAIMABLE_PHASES))

_UPSERT_SQL = text(
    """
    INSERT INTO plans
        (id, version, phase, iteration, data, retry_not_before, paused,
         created_at, updated_at)
    VALUES (:id, :version, :phase, :iteration, :data, :retry_not_before, :paused,
            :now, :now)
    ON CONFLICT(id) DO UPDATE SET
        version   = excluded.version,
        phase     = excluded.phase,
        iteration = excluded.iteration,
        data      = excluded.data,
        retry_not_before = excluded.retry_not_before,
        paused    = excluded.paused,
        updated_at = excluded.updated_at
    WHERE plans.version < excluded.version
    """
)

_CLAIM_SQL = text(
    f"""
    UPDATE plans
    SET claimed_by = :worker_id,
        claimed_at = :now_epoch,
        lease_expires_at = :expires_epoch,
        lease_seconds = :lease_seconds
    WHERE id = (
        SELECT id FROM plans
        WHERE phase IN {_CLAIMABLE_PHASES!r}
          AND (claimed_by IS NULL OR lease_expires_at < :now_epoch)
          AND (retry_not_before IS NULL OR retry_not_before < :now_epoch)
          AND paused = 0
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

    # --- UnitOfWork binding ---
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

    # --- persistence (inside the UoW transaction) ---
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
                "version": plan.version,
                "phase": plan.phase.value,
                "iteration": plan.iteration,
                "data": plan.model_dump_json(),
                "retry_not_before": (
                    int(plan.planning_retry_not_before.timestamp())
                    if plan.planning_retry_not_before is not None
                    else None
                ),
                "paused": 1 if plan.paused else 0,
                "now": self._clock.now().isoformat(),
            },
        )
        if result.rowcount == 0:
            stored = session.execute(
                text("SELECT version FROM plans WHERE id = :id"), {"id": plan.id}
            ).scalar_one()
            raise StaleVersionError(plan.id, plan.version, int(stored))

    # --- create idempotency (inside the UoW transaction) ---
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

    # --- lease (own short transactions; called OUTSIDE the UoW) ---
    def claim_one_unit(self, worker_id: str, lease_seconds: int) -> Plan | None:
        now_epoch = int(self._clock.now().timestamp())

        def _claim(session: Session) -> str | None:
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

        data = run_in_session(self._session_factory, _claim)
        if data is None:
            return None
        return PlanFactory.reconstruct(json.loads(data))

    def heartbeat(self, plan_id: str, worker_id: str) -> None:
        now_epoch = int(self._clock.now().timestamp())
        run_in_session(
            self._session_factory,
            lambda s: s.execute(
                _HEARTBEAT_SQL,
                {"plan_id": plan_id, "worker_id": worker_id, "now_epoch": now_epoch},
            ),
        )

    def release(self, plan_id: str, worker_id: str) -> None:
        run_in_session(
            self._session_factory,
            lambda s: s.execute(
                _RELEASE_SQL, {"plan_id": plan_id, "worker_id": worker_id}
            ),
        )

    # --- read-side extras (not part of the PlanRepository port) ---
    def list_summaries(self) -> list[dict[str, object]]:
        """Cheap listing off the promoted columns — no document parsing."""
        with self._session_factory() as session:
            rows = session.execute(
                text(
                    "SELECT id, phase, iteration, version, claimed_by, updated_at,"
                    " paused FROM plans ORDER BY updated_at DESC"
                )
            ).all()
        return [
            {
                "id": r[0],
                "phase": r[1],
                "iteration": r[2],
                "version": r[3],
                "claimed_by": r[4],
                "updated_at": r[5],
                "paused": bool(r[6]),
            }
            for r in rows
        ]
