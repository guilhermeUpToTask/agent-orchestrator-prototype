"""
praxis_orchestrator/infra/worker/main.py — the worker entrypoint (the orchestration cadence).

Wraps the transport-agnostic worker_tick with the real sleep/claim rhythm:
tick; if no progress was made, sleep poll_seconds and tick again. That sleep is
the ONLY polling in the system — and only between plans, never within one
(within a claimed plan the drive loop runs unit-to-unit without waiting).

Crash recovery needs no supervisor logic here: a dead worker's lease expires
and any other worker's next tick reclaims the plan from persisted state.

Domain unfreeze #14 (symmetric per-goal leases) removed the plan-level tick's
execution fallback entirely for a cyclic plan — a lone `worker_tick` no
longer drives ANY task execution once a cycle is active; every ready goal is
claimed and driven exclusively through `claim_ready_goal`/`drive_goal`. So
this loop now ALSO owns a small in-process goal-worker POOL: each iteration,
after `worker_tick`, it reaps finished goal-drivers and claims+spawns new
ones up to `max_concurrent_goals`, running them as concurrent asyncio tasks
in this same process/event loop. This is what makes a single
`praxis worker start` internally parallel across independent goals —
operators no longer need to hand-start a second OS process for that (running
more processes is still supported for horizontal/multi-host scaling; the
`goal_leases` row is a real cross-process lease either way).

CRITICAL: a `UnitOfWork` is not thread-safe (see CLAUDE.md) and, in practice,
not safe to share across concurrently-running asyncio tasks either — a
SQLAlchemy Session mid-`with uow:` on one task must never be touched by
another coroutine's `with uow:` at the same time. `worker_tick` keeps its own
`uow` (created once, reused tick-to-tick — always sequential, never
concurrent with itself). Every spawned goal-worker task gets its OWN fresh
`container.new_unit_of_work()`, never the shared one.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import structlog

from praxis_orchestrator.app.handlers.planning_handler import PlanningHandler
from praxis_orchestrator.app.use_cases.reconcile_runtime import reconcile_stale_attempts
from praxis_orchestrator.domain.errors.tasks_errors import StaleVersionError
from praxis_orchestrator.infra.container import AppContainer
from praxis_orchestrator.infra.runtime.dependency_checker import check_dependencies
from praxis_orchestrator.infra.runtime.factory import validate_agent_runner_mode

# Deliberately independent of poll_seconds, which defaults to 1.0 and is driven
# far lower in tests: a heartbeat is liveness, not a poll, and there is no value
# in a write per tick against the database the claim scan is tuned around.
_HEARTBEAT_SECONDS = 5.0

log = structlog.get_logger(__name__)


async def run_worker_forever(
    container: AppContainer,
    worker_id: str = "worker-1",
    poll_seconds: float = 1.0,
    lease_seconds: int = 300,
    stop: asyncio.Event | None = None,
    max_concurrent_goals: int = 4,
) -> None:
    """Run the claim-and-drive loop until `stop` is set (or forever).

    Active planning and execution actions renew their claim every one-third of
    lease_seconds; startup reconciliation never abandons attempts behind a live
    claim.

    `max_concurrent_goals` bounds this process's own in-process goal-worker
    pool. Like `claim_ready_goal`'s `scan_limit`, this is NOT derived from
    load testing yet (ROADMAP-flagged: tune empirically once real contention
    is observable) — small and conservative by default.
    """
    from praxis_orchestrator.app.use_cases.claim_ready_goal import claim_ready_goal
    from praxis_orchestrator.app.use_cases.run_worker import drive_goal, worker_tick

    uow = container.new_unit_of_work()
    planning_handler = PlanningHandler(
        container.reasoner,
        container.agent_repo,
        container.capability_repo,
        container.clock,
        container.provider_capacity_policy,
        container.planning_artifacts,
        # Enrichment fans out exactly as wide as this process drives goals, so
        # the two halves of one worker cannot disagree about its own budget.
        max_concurrent_enrichment=max_concurrent_goals,
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
        max_concurrent_goals=max_concurrent_goals,
    )

    # (plan_id, goal_id) -> in-flight asyncio.Task driving that goal. Each
    # entry owns its own UnitOfWork (captured in the closure below), never
    # the shared `uow` above.
    inflight: dict[asyncio.Task[tuple[str, int]], tuple[str, str]] = {}

    async def _run_goal(plan_id: str, goal_id: str) -> tuple[str, int]:
        goal_uow = container.new_unit_of_work()
        try:
            result = await drive_goal(
                plan_id,
                goal_id,
                goal_uow,
                container.execution_services,
                worker_id,
                lease_seconds=lease_seconds,
            )
            if result[0] == "lease_lost":
                log.info(
                    "worker.goal_lease_lost",
                    worker_id=worker_id,
                    plan_id=plan_id,
                    goal_id=goal_id,
                )
            return result
        except StaleVersionError:
            log.info(
                "worker.goal_claim_contention",
                worker_id=worker_id,
                plan_id=plan_id,
                goal_id=goal_id,
            )
            return ("contention", 0)
        finally:
            # drive_goal does not release its own lease (goal_tick used to be
            # the caller responsible for that) -- always release here so a
            # finished/crashed goal-worker doesn't hold its lease until
            # natural expiry.
            goal_uow.goal_leases.release(plan_id, goal_id, worker_id)

    async def _heartbeat() -> None:
        """Liveness, reported from its OWN task rather than the loop body.

        The coordinator loop below parks in `asyncio.wait(FIRST_COMPLETED)` for
        the whole of a long goal, so a beat written inline would go silent for
        exactly that window and report a BUSY worker as dead — an operator would
        restart a worker that is working.

        `WorkerRegistry.beat` writes through `asyncio.to_thread`, which matters
        here for the same reason the loop below refuses to claim while goals are
        in flight: a blocking SQLite write on the event-loop thread can wedge
        against a lock whose owner is a coroutine waiting for that same loop.
        The write is best-effort — it logs and returns rather than raising, so a
        telemetry hiccup never takes down a working worker.
        """
        while stop is None or not stop.is_set():
            await container.worker_registry.beat(
                worker_id,
                mode=runner_mode.mode,
                poll_seconds=poll_seconds,
                lease_seconds=lease_seconds,
                max_concurrent_goals=max_concurrent_goals,
                inflight_goals=len(inflight),
                now=container.clock.now(),
            )
            await asyncio.sleep(_HEARTBEAT_SECONDS)

    heartbeat = asyncio.ensure_future(_heartbeat())

    while stop is None or not stop.is_set():
        # SQLite access is synchronous. Do not let the coordinator perform a
        # plan claim (or another goal-claim scan) while goal tasks are in
        # flight: an unlucky writer-lock handoff can block this event-loop
        # thread inside SQLite while the lock owner's coroutine is waiting for
        # the same loop to resume, producing a self-deadlock. Goals already
        # claimed in this wave still run concurrently; coordination resumes
        # after the wave drains.
        if inflight:
            done, _pending = await asyncio.wait(
                inflight.keys(),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                plan_id, goal_id = inflight.pop(task)
                try:
                    task.result()
                except Exception:
                    log.error(
                        "worker.goal_worker_failed",
                        worker_id=worker_id,
                        plan_id=plan_id,
                        goal_id=goal_id,
                        exc_info=True,
                    )
            if inflight:
                continue

        try:
            progressed = await worker_tick(
                uow,
                container.execution_services,
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

        # Claim and spawn new goal-workers up to this process's own cap.
        # claim_ready_goal manages its own short-lived transaction per call
        # (see its own docstring) -- using the shared `uow` here is fine
        # since this claim scan is sequential with worker_tick above, never
        # concurrent with itself or with the spawned tasks' own UoWs.
        goal_progressed = False
        free_slots = max_concurrent_goals
        try:
            for _ in range(max(0, free_slots)):
                claimed = claim_ready_goal(uow, worker_id, lease_seconds, container.clock)
                if claimed is None:
                    break
                plan_id, goal_id = claimed
                task = asyncio.ensure_future(_run_goal(plan_id, goal_id))
                inflight[task] = (plan_id, goal_id)
                goal_progressed = True  # a fresh claim is progress even before it finishes
        except Exception:
            # The SAME guarantee the tick above already had, and this scan was
            # missing it: one bad plan must not kill the worker. Observed live —
            # a plan deleted between the readiness scan and the lease INSERT
            # raised `FOREIGN KEY constraint failed` out of `claim_ready_goal`,
            # which is outside the tick's guard, so the exception unwound the
            # whole loop and the process exited 1. Under the dev supervisor that
            # also took the API down with it. A delete racing a claim is normal
            # (`DELETE /api/plans/{id}` cascades while a scan is in flight), so
            # this must be survivable, not fatal.
            log.error("worker.goal_claim_scan_failed", worker_id=worker_id, exc_info=True)

        if not progressed and not goal_progressed and not inflight:
            await asyncio.sleep(poll_seconds)

    if inflight:
        log.info("worker.draining_goal_pool", worker_id=worker_id, count=len(inflight))
        await asyncio.gather(*inflight.keys(), return_exceptions=True)
    heartbeat.cancel()
    with suppress(asyncio.CancelledError):
        await heartbeat
    log.info("worker.stopped", worker_id=worker_id)
