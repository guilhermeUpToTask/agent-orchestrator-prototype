"""
src/api/routers/workers.py — GET /api/workers: is anything going to pick up work?

`GET /api/readiness` answers whether this machine *can* run a plan. It cannot
answer whether anything *will*: plan claims and goal leases prove a worker is
BUSY, and an idle worker holds neither, so before the first claim "worker
running, nothing to do" and "worker never started" looked identical over HTTP —
the most common way a local install is silently half-configured.

Staleness is computed HERE, against the server clock, for the same reason
`worker_lease.expired` is: the client's clock is not the server's, and a
timestamp alone makes every consumer re-derive the threshold.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent_orchestrator.api.dependencies import get_container
from agent_orchestrator.infra.container import AppContainer

router = APIRouter(tags=["workers"])

# Three missed beats (see `_HEARTBEAT_SECONDS` in src/infra/worker/main.py):
# long enough to absorb a slow tick or a lock retry, short enough that a crashed
# worker is visible inside an operator's attention span.
STALE_AFTER_SECONDS = 15.0


class WorkerStatusResponse(BaseModel):
    worker_id: str
    mode: str
    started_at: str
    last_seen_at: str
    poll_seconds: float
    lease_seconds: int
    max_concurrent_goals: int
    inflight_goals: int
    seconds_since_seen: float
    stale: bool


@router.get("/workers", response_model=list[WorkerStatusResponse])
def list_workers(
    container: AppContainer = Depends(get_container),
) -> list[WorkerStatusResponse]:
    now = container.clock.now()
    return [
        WorkerStatusResponse(
            worker_id=row.worker_id,
            mode=row.mode,
            started_at=row.started_at.isoformat(),
            last_seen_at=row.last_seen_at.isoformat(),
            poll_seconds=row.poll_seconds,
            lease_seconds=row.lease_seconds,
            max_concurrent_goals=row.max_concurrent_goals,
            inflight_goals=row.inflight_goals,
            seconds_since_seen=(seconds := (now - row.last_seen_at).total_seconds()),
            stale=seconds > STALE_AFTER_SECONDS,
        )
        for row in container.worker_registry.list_workers()
    ]
