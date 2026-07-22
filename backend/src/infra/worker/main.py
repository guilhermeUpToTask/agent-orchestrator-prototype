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
from src.app.use_cases.reconcile_runtime import reconcile_stale_attempts
from src.infra.container import AppContainer
from src.infra.runtime.dependency_checker import check_dependencies
from src.infra.runtime.factory import validate_agent_runner_mode

log = structlog.get_logger(__name__)


async def run_worker_forever(
    container: AppContainer,
    worker_id: str = "worker-1",
    poll_seconds: float = 1.0,
    lease_seconds: int = 300,
    stop: asyncio.Event | None = None,
) -> None:
    """Run the claim-and-drive loop until `stop` is set (or forever).

    Active planning and execution actions renew their claim every one-third of
    lease_seconds; startup reconciliation never abandons attempts behind a live
    claim.
    """
    from src.app.use_cases.run_worker import goal_tick, worker_tick

    uow = container.new_unit_of_work()
    planning_handler = PlanningHandler(
        container.reasoner,
        container.agent_repo,
        container.capability_repo,
        container.clock,
    )
    runner_mode = validate_agent_runner_mode(container.config_store)
    reconciled = reconcile_stale_attempts(container.new_unit_of_work(), container.clock)
    if reconciled:
        log.warning("worker.stale_attempts_reconciled", count=len(reconciled))
    prune = getattr(container.workspace, "prune", None)
    if prune is not None:
        await prune()
    audit = getattr(container.workspace, "audit", None)
    if audit is not None:
        audit_result = await audit()
        log.info("worker.workspace_audited", project_count=len(audit_result))
    if runner_mode.mode == "real":
        # Warn-only probes: dry-run needs no binaries, and a missing runtime
        # surfaces per task as a classified TaskFailed anyway.
        for dep in check_dependencies().failing():
            log.warning(
                "worker.dependency_missing",
                name=dep.name,
                binary=dep.binary,
                message=dep.message,
                install_hint=dep.install_hint,
            )
    log.info(
        "worker.started",
        worker_id=worker_id,
        agent_runner_mode=runner_mode.mode,
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
                verifier=container.verification_executor,
            )
        except Exception:
            # One poisoned plan must not kill the worker: the tick's finally
            # already released the claim; log, back off a poll, keep serving
            # the other plans. Retry churn is poll-cadence-bounded.
            log.error("worker.tick_failed", worker_id=worker_id, exc_info=True)
            progressed = False
        # Goal-level parallelism (ADR-001, domain unfreeze #12): a plan-level
        # tick no longer drives cyclic execution once every ready goal is
        # already enriched (advance_plan.py) -- goal_tick is what actually
        # claims and drives those goals. Running both ticks from the SAME
        # worker process means a single `orchestrate worker start` keeps
        # executing cyclic plans exactly as before; running MULTIPLE
        # processes is what turns this into real cross-process goal
        # parallelism, since goal_tick's claim is per-goal, not per-plan.
        try:
            goal_progressed = await goal_tick(
                uow,
                container.agent_runner,
                container.agent_repo,
                container.workspace,
                container.agent_event_sink,
                container.clock,
                worker_id,
                lease_seconds,
                verifier=container.verification_executor,
            )
        except Exception:
            log.error("worker.goal_tick_failed", worker_id=worker_id, exc_info=True)
            goal_progressed = False
        if not progressed and not goal_progressed:
            await asyncio.sleep(poll_seconds)
    log.info("worker.stopped", worker_id=worker_id)
