"""run_worker — the orchestrator loop (Option A: loop-driven, pull-based).

This is what REPLACES the old push-dispatch + reconciler. A worker:
  1. claims a plan that needs work (lease — crash recovery for dead workers),
  2. drives advance_plan in a loop until the plan pauses/finishes/fails,
  3. heartbeats the lease each unit (so a long-but-alive plan isn't stolen),
  4. releases on pause/done/fail/crash.

Within a plan, advancing is the `while signal == "continue"` loop — NO polling,
NO goal "trying to start". next_action pulls the next ready unit; an unready goal
is simply never selected. That's the structural fix for the pending-goal noise.

This function is transport-agnostic: the real worker entrypoint wraps it with the
actual sleep/claim cadence; tests drive it directly.
"""
from __future__ import annotations

from application.ports import (
    AgentEventSink,
    AgentRunner,
    Clock,
    UnitOfWork,
    Workspace,
)
from application.use_cases.advance_plan import advance_plan
from domain.repositories.agent_repo import AgentRepository


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
) -> str:
    """Advance one plan until it stops making progress. Returns the terminal
    signal ('paused' | 'not_ready' | 'done' | 'failed'). Heartbeats each unit.

    'not_ready' means the plan has work but everything is backing off — the worker
    releases it and a later tick re-checks (the durable retry gate decides when)."""
    signal = "continue"
    steps = 0
    while signal == "continue" and steps < max_steps:
        signal = await advance_plan(plan_id, uow, runner, agents, workspace, event_sink, clock)
        uow.plans.heartbeat(plan_id, worker_id)   # renew lease while alive
        steps += 1
    return signal


async def worker_tick(
    uow: UnitOfWork,
    runner: AgentRunner,
    agents: AgentRepository,
    workspace: Workspace,
    event_sink: AgentEventSink,
    clock: Clock,
    worker_id: str,
    lease_seconds: int = 60,
) -> bool:
    """One claim-and-drive cycle. Returns True if it did work, False if no plan
    was available (caller sleeps then ticks again). The real worker entrypoint
    loops this with asyncio.sleep on the False branch — that sleep is the ONLY
    polling, and only between plans, never within one."""
    plan = uow.plans.claim_one_unit(worker_id, lease_seconds)
    if plan is None:
        return False
    try:
        await drive_plan(plan.id, uow, runner, agents, workspace, event_sink, clock, worker_id)
    finally:
        uow.plans.release(plan.id, worker_id)  # free on pause/done/fail/crash
    return True
