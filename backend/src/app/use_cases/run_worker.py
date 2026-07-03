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

from src.app.handlers.base import PhaseHandler
from src.app.ports import (
    AgentEventSink,
    AgentRunner,
    Clock,
    UnitOfWork,
    Workspace,
)
from src.app.use_cases.advance_plan import advance_plan
from src.domain.repositories.agent_repo import AgentRepository


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
) -> tuple[str, int]:
    """Advance one plan until it stops making progress. Returns (terminal signal,
    units advanced) — the signal is 'paused' | 'not_ready' | 'done' | 'failed';
    the count is how many CONTINUE steps actually did work. Heartbeats each unit.

    'not_ready' means the plan has work but everything is backing off — the worker
    releases it and a later tick re-checks (the durable retry gate decides when)."""
    signal = "continue"
    progressed = 0
    while signal == "continue" and progressed < max_steps:
        signal = await advance_plan(
            plan_id, uow, runner, agents, workspace, event_sink, clock,
            planning_handler,
        )
        uow.plans.heartbeat(plan_id, worker_id)   # renew lease while alive
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
            plan.id, uow, runner, agents, workspace, event_sink, clock, worker_id,
            planning_handler=planning_handler,
        )
    finally:
        uow.plans.release(plan.id, worker_id)  # free on pause/done/fail/crash
    return progressed > 0 or signal in ("done", "failed")
