"""
src/infra/worker/main.py — the worker entrypoint (the orchestration cadence).

Wraps the transport-agnostic worker_tick with the real sleep/claim rhythm:
tick; if no progress was made, sleep poll_seconds and tick again. That sleep is
the ONLY polling in the system — and only between plans, never within one
(within a claimed plan the drive loop runs unit-to-unit without waiting).

Crash recovery needs no supervisor logic here: a dead worker's lease expires
and any other worker's next tick reclaims the plan from persisted state.
"""
from __future__ import annotations

import asyncio

import structlog

from src.app.handlers.planning_handler import PlanningHandler
from src.infra.container import AppContainer

log = structlog.get_logger(__name__)


async def run_worker_forever(
    container: AppContainer,
    worker_id: str = "worker-1",
    poll_seconds: float = 1.0,
    lease_seconds: int = 300,
    stop: asyncio.Event | None = None,
) -> None:
    """Run the claim-and-drive loop until `stop` is set (or forever).

    lease_seconds must exceed the longest expected single task run: heartbeats
    happen between units, never mid-agent-run (mid-run heartbeats are roadmap
    Phase 3).
    """
    from src.app.use_cases.run_worker import worker_tick

    uow = container.new_unit_of_work()
    planning_handler = PlanningHandler(
        container.reasoner, container.agent_repo, container.capability_repo
    )
    log.info(
        "worker.started",
        worker_id=worker_id,
        poll_seconds=poll_seconds,
        lease_seconds=lease_seconds,
    )
    while stop is None or not stop.is_set():
        try:
            progressed = await worker_tick(
                uow,
                container.agent_runner,
                container.agent_repo,
                container.workspace,
                container.agent_event_sink,
                container.clock,
                worker_id,
                lease_seconds,
                planning_handler=planning_handler,
            )
        except Exception:
            # One poisoned plan must not kill the worker: the tick's finally
            # already released the claim; log, back off a poll, keep serving
            # the other plans. Retry churn is poll-cadence-bounded.
            log.error("worker.tick_failed", worker_id=worker_id, exc_info=True)
            progressed = False
        if not progressed:
            await asyncio.sleep(poll_seconds)
    log.info("worker.stopped", worker_id=worker_id)
