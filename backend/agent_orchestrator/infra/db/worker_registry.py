"""
src/infra/db/worker_registry.py — WorkerRegistry, the `workers` table adapter.

A worker's liveness beat is BEST-EFFORT telemetry, exactly like
`SqliteAgentEventSink` (`agent_orchestrator/infra/db/agent_event_sink.py`): written on its own
connection, via `run_in_session` (which already carries the lock-retry
policy), moved off the event loop with `asyncio.to_thread`, and never allowed
to propagate — a telemetry hiccup must not kill a worker that is otherwise
doing real work. `beat` therefore returns `None` on both success and failure;
callers that want to know whether the fleet is healthy read `list_workers`
(or, later, `GET /api/workers`), not the return value of `beat`.

The `workers` table is NOT plan-scoped: no FK to `plans`, no cascade
obligation (migration 0016). Rows are UPSERTED by `worker_id`, never
appended — a restarted worker replaces its own row rather than leaving a
trail, so the table stays the size of the fleet, not its uptime.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from agent_orchestrator.infra.db._session import run_in_session

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class WorkerRow:
    worker_id: str
    mode: str
    started_at: datetime
    last_seen_at: datetime
    poll_seconds: float
    lease_seconds: int
    max_concurrent_goals: int
    inflight_goals: int


# `started_at` is deliberately absent from the UPDATE branch: a restarted
# worker keeps its ORIGINAL boot time visible only because the row is updated
# in place rather than replaced wholesale. That is acceptable — arguably
# correct — because the operator-facing signal is `last_seen_at` ("is it
# still reporting?"), and if the process identity actually changed, it would
# report under a different `worker_id` rather than reusing this one.
_BEAT_SQL = text(
    """
    INSERT INTO workers (worker_id, mode, started_at, last_seen_at, poll_seconds,
                         lease_seconds, max_concurrent_goals, inflight_goals)
    VALUES (:worker_id, :mode, :now, :now, :poll_seconds,
            :lease_seconds, :max_concurrent_goals, :inflight_goals)
    ON CONFLICT(worker_id) DO UPDATE SET
        mode = excluded.mode,
        last_seen_at = excluded.last_seen_at,
        poll_seconds = excluded.poll_seconds,
        lease_seconds = excluded.lease_seconds,
        max_concurrent_goals = excluded.max_concurrent_goals,
        inflight_goals = excluded.inflight_goals
    """
)

_LIST_SQL = text(
    """
    SELECT worker_id, mode, started_at, last_seen_at, poll_seconds,
           lease_seconds, max_concurrent_goals, inflight_goals
    FROM workers
    ORDER BY worker_id
    """
)


class WorkerRegistry:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    async def beat(
        self,
        worker_id: str,
        *,
        mode: str,
        poll_seconds: float,
        lease_seconds: int,
        max_concurrent_goals: int,
        inflight_goals: int,
        now: datetime | None = None,
    ) -> None:
        """`now` is injected so the timestamp written here and the staleness
        computed in `routers/workers.py` come from the SAME clock. They are both
        wall clock in production, but a test that pins the container clock would
        otherwise compare an injected `now` against a real one and measure the
        gap between them rather than the worker's silence."""
        params = {
            "worker_id": worker_id,
            "mode": mode,
            "now": (now or datetime.now(timezone.utc)).isoformat(),
            "poll_seconds": poll_seconds,
            "lease_seconds": lease_seconds,
            "max_concurrent_goals": max_concurrent_goals,
            "inflight_goals": inflight_goals,
        }
        try:
            await asyncio.to_thread(
                run_in_session,
                self._sf,
                lambda s: s.execute(_BEAT_SQL, params),
            )
        except Exception:
            # best-effort: telemetry loss is logged, never propagated — a
            # worker that cannot report its own liveness must still keep
            # claiming and running work.
            log.warning(
                "worker_registry.beat_failed",
                worker_id=worker_id,
                exc_info=True,
            )

    def list_workers(self) -> list[WorkerRow]:
        with self._sf() as session:
            rows = session.execute(_LIST_SQL).all()
        return [
            WorkerRow(
                worker_id=str(row.worker_id),
                mode=str(row.mode),
                started_at=datetime.fromisoformat(str(row.started_at)),
                last_seen_at=datetime.fromisoformat(str(row.last_seen_at)),
                poll_seconds=float(row.poll_seconds),
                lease_seconds=int(row.lease_seconds),
                max_concurrent_goals=int(row.max_concurrent_goals),
                inflight_goals=int(row.inflight_goals),
            )
            for row in rows
        ]
