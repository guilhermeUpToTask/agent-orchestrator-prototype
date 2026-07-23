"""run_worker — the orchestrator loop (Option A: loop-driven, pull-based).

This is what REPLACES the old push-dispatch + reconciler. A worker:
  1. claims a plan that needs work (lease — crash recovery for dead workers),
  2. drives advance_plan in a loop until the plan pauses/finishes/fails,
  3. heartbeats the lease each unit (so a long-but-alive plan isn't stolen),
  4. releases on pause/done/fail/crash.

Within a plan, advancing is the `while signal == "continue"` loop — NO polling,
NO goal "trying to start". next_action pulls the next ready unit; an unready goal
is simply never selected. That's the structural fix for the pending-goal noise.

The claim predicate (in the repository) is the DRIVER MODEL: only ARCHITECTURE,
ENRICHING and RUNNING are worker-claimable. Conversational phases (DISCOVERY,
REPLANNING) and the human gates are invisible to workers — what isn't ready is
never selected, so it never churns.

This function is transport-agnostic: the real worker entrypoint wraps it with the
actual sleep/claim cadence; tests drive it directly.
"""

from __future__ import annotations

import asyncio

from src.app.handlers.base import PhaseHandler
from src.app.handlers.execution_handler import ExecutionHandler
from src.app.ports import (
    AgentEventSink,
    AgentRunner,
    Clock,
    UnitOfWork,
    Workspace,
    VerificationExecutor,
)
from src.app.use_cases.advance_plan import advance_plan
from collections.abc import Awaitable, Callable

from src.domain.repositories.agent_repo import AgentRepository


async def _advance_with_heartbeats(
    heartbeat_interval_seconds: float,
    heartbeat: Callable[[], bool | None],
    advance: Awaitable[str],
) -> tuple[str, bool]:
    """Renew a lease (via the given `heartbeat` callback) while one atomic
    action is still running. Generalized (domain unfreeze #12 / Phase 3c) so
    both the plan-level (`drive_plan`) and goal-level (`drive_goal`) loops
    share this, instead of each hardcoding its own lease repository call."""
    task = asyncio.ensure_future(advance)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=heartbeat_interval_seconds)
            if task in done:
                return await task, False
            if heartbeat() is False:
                return await task, True
    except BaseException:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        raise


async def drive_plan(
    plan_id: str,
    uow: UnitOfWork,
    runner: AgentRunner,
    agents: AgentRepository,
    workspace: Workspace,
    event_sink: AgentEventSink,
    clock: Clock,
    worker_id: str,
    max_steps: int = 10_000,
    planning_handler: PhaseHandler | None = None,
    lease_seconds: int = 60,
    heartbeat_interval_seconds: float | None = None,
    verifier: VerificationExecutor | None = None,
) -> tuple[str, int]:
    """Advance one plan until it stops making progress. Returns (terminal signal,
    units advanced) — the signal is 'paused' | 'not_ready' | 'done' | 'failed';
    the count is how many CONTINUE steps actually did work. Heartbeats each unit.

    'not_ready' means the plan has work but everything is backing off — the worker
    releases it and a later tick re-checks (the durable retry gate decides when)."""
    signal = "continue"
    heartbeat_interval_seconds = (
        max(1.0, lease_seconds / 3)
        if heartbeat_interval_seconds is None
        else heartbeat_interval_seconds
    )
    progressed = 0
    while signal == "continue" and progressed < max_steps:
        signal, _ = await _advance_with_heartbeats(
            heartbeat_interval_seconds,
            lambda: uow.plans.heartbeat(plan_id, worker_id),
            advance_plan(
                plan_id,
                uow,
                runner,
                agents,
                workspace,
                event_sink,
                clock,
                planning_handler,
                verifier,
            ),
        )
        uow.plans.heartbeat(plan_id, worker_id)
        if signal == "continue":
            progressed += 1
    return signal, progressed


async def worker_tick(
    uow: UnitOfWork,
    runner: AgentRunner,
    agents: AgentRepository,
    workspace: Workspace,
    event_sink: AgentEventSink,
    clock: Clock,
    worker_id: str,
    lease_seconds: int = 60,
    planning_handler: PhaseHandler | None = None,
    verifier: VerificationExecutor | None = None,
) -> bool:
    """One claim-and-drive cycle. Returns True only if actual work ADVANCED —
    not merely because a plan was claimed. A claim that immediately came back
    'not_ready'/'paused' with zero steps returns False so the caller sleeps;
    returning True there produced a hot claim→release CPU spin on any plan whose
    work was entirely backing off (the verified worker-tick spin bug)."""
    plan = uow.plans.claim_one_unit(worker_id, lease_seconds)
    if plan is None:
        return False
    try:
        signal, progressed = await drive_plan(
            plan.id,
            uow,
            runner,
            agents,
            workspace,
            event_sink,
            clock,
            worker_id,
            planning_handler=planning_handler,
            verifier=verifier,
            lease_seconds=lease_seconds,
        )
    finally:
        uow.plans.release(plan.id, worker_id)  # free on pause/done/fail/crash
    return progressed > 0 or signal in ("done", "failed")


async def _advance_goal(
    plan_id: str,
    goal_id: str,
    uow: UnitOfWork,
    execution: ExecutionHandler,
) -> str:
    with uow:
        plan = uow.plans.get(plan_id)
    signal = await execution.handle_goal(plan_id, goal_id, plan, uow)
    return signal.value


async def drive_goal(
    plan_id: str,
    goal_id: str,
    uow: UnitOfWork,
    runner: AgentRunner,
    agents: AgentRepository,
    workspace: Workspace,
    event_sink: AgentEventSink,
    clock: Clock,
    worker_id: str,
    max_steps: int = 10_000,
    lease_seconds: int = 60,
    heartbeat_interval_seconds: float | None = None,
    verifier: VerificationExecutor | None = None,
) -> tuple[str, int]:
    """Goal-level analog of `drive_plan` (ADR-001, domain unfreeze #12 /
    Phase 3c): advance ONE goal (within an active cycle) until it stops
    making progress, holding that goal's lease instead of the whole plan's.
    Never routes to planning/gates — a goal-lease holder only ever drives
    execution for its one goal."""
    execution = ExecutionHandler(runner, agents, workspace, event_sink, clock, verifier)
    signal = "continue"
    heartbeat_interval_seconds = (
        max(1.0, lease_seconds / 3)
        if heartbeat_interval_seconds is None
        else heartbeat_interval_seconds
    )
    progressed = 0
    while signal == "continue" and progressed < max_steps:
        signal, lease_lost = await _advance_with_heartbeats(
            heartbeat_interval_seconds,
            lambda: uow.goal_leases.heartbeat(
                plan_id, goal_id, worker_id, lease_seconds, clock.now()
            ),
            _advance_goal(plan_id, goal_id, uow, execution),
        )
        if not lease_lost:
            lease_lost = not uow.goal_leases.heartbeat(
                plan_id, goal_id, worker_id, lease_seconds, clock.now()
            )
        if signal == "continue":
            progressed += 1
        if lease_lost:
            return "lease_lost", progressed
    return signal, progressed

