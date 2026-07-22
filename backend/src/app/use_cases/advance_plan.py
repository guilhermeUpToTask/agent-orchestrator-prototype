"""advance_plan — thin phase DISPATCHER (one unit of work).

Routes on plan.phase to the handler that owns that phase, then returns the handler's
Signal to the worker loop. This replaced the old god-function: task execution,
planning, and gates are now separate handlers, so adding a phase never touches the
others.

  RUNNING                                   -> ExecutionHandler (the pull-scan loop)
  DISCOVERY/ARCHITECTURE/ENRICHING/REPLANNING -> PlanningHandler (reasoner; Phase 2.5 seam)
  AWAITING_REVIEW/REVIEW                    -> GateHandler (pause/resume)
  DONE/FAILED                               -> terminal (return the terminal signal)

Backwards-compatible functional ent/ry point `advance_plan(...)` is kept so the worker
loop and existing tests call it unchanged; it builds the dispatcher and delegates.
"""

from __future__ import annotations

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.repositories.agent_repo import AgentRepository
from src.domain.services.navigation import ready_goal_ids

from src.app.handlers.base import PhaseHandler, Signal
from src.app.handlers.execution_handler import ExecutionHandler
from src.app.handlers.gate_handler import GateHandler
from src.app.ports import (
    AgentEventSink,
    AgentRunner,
    Clock,
    UnitOfWork,
    Workspace,
    VerificationExecutor,
)

# Phase groups -> which handler owns them.
_PLANNING_PHASES = frozenset(
    {
        PlanPhase.DISCOVERY,
        PlanPhase.ARCHITECTURE,
        PlanPhase.ENRICHING,
        PlanPhase.REPLANNING,
    }
)
_GATE_PHASES = frozenset({PlanPhase.AWAITING_REVIEW, PlanPhase.REVIEW})
_TERMINAL_PHASES = frozenset({PlanPhase.DONE, PlanPhase.FAILED})


class PlanDispatcher:
    """Owns the handler wiring and the phase->handler routing."""

    def __init__(
        self,
        runner: AgentRunner,
        agents: AgentRepository,
        workspace: Workspace,
        event_sink: AgentEventSink,
        clock: Clock,
        planning_handler: PhaseHandler | None = None,
        verifier: VerificationExecutor | None = None,
    ) -> None:
        self._execution = ExecutionHandler(runner, agents, workspace, event_sink, clock, verifier)
        self._gate = GateHandler()
        self._planning = planning_handler  # injected when the reasoner exists (Phase 2.5)
        self._clock = clock

    async def advance(self, plan_id: str, uow: UnitOfWork) -> Signal:
        with uow:
            plan: Plan = uow.plans.get(plan_id)
            phase = plan.phase

        if plan.active_cycle is not None:
            if plan.status.value != "running" or plan.pause_requested:
                return Signal.PAUSED
            goals = plan.execution_goals
            # Goal-level parallelism (ADR-001, domain unfreeze #12) is PURELY
            # ADDITIVE here: this plan-level tick keeps driving execution
            # exactly as it always has (next_action's single-earliest-ready
            # result, unchanged since Phase 1) so a lone worker process (no
            # separate goal-lease worker running) behaves byte-identically to
            # before this unfreeze. The only change is routing to planning
            # when ANY ready goal needs enrichment (not just the
            # earliest-position one — the actual fix for the fan-out gap
            # Phase 2 addressed inside PlanningHandler, but the DISPATCHER
            # must also stop assuming "earliest non-terminal goal has no
            # tasks yet" is the only case that needs a planning tick).
            # Separate goal_tick workers (run_worker.py) ADDITIONALLY claim
            # and drive OTHER ready-and-enriched goals concurrently — this
            # tick never needs to know whether any exist.
            ready_ids = ready_goal_ids(goals, self._clock.now())
            needs_enrichment = any(goal.id in ready_ids and not goal.tasks for goal in goals)
            if needs_enrichment:
                if self._planning is None:
                    return Signal.PAUSED
                return await self._planning.handle(plan_id, plan, uow)
            return await self._execution.handle(plan_id, plan, uow)

        if (
            plan.status.value == "running"
            and plan.intent_proposal is not None
            and plan.intent_proposal.approved_at is not None
            and plan.cycle_draft is None
        ):
            if self._planning is None:
                return Signal.PAUSED
            return await self._planning.handle(plan_id, plan, uow)

        if phase in _TERMINAL_PHASES:
            return Signal.DONE if phase == PlanPhase.DONE else Signal.FAILED

        if plan.paused:
            # Pause gate armed (the claim predicate normally filters these; this
            # covers a pause landing after the claim): release without dispatching.
            return Signal.PAUSED

        if phase == PlanPhase.RUNNING:
            return await self._execution.handle(plan_id, plan, uow)

        if phase in _GATE_PHASES:
            return await self._gate.handle(plan_id, plan, uow)

        if phase in _PLANNING_PHASES:
            if self._planning is None:
                # Seam not yet wired (pre-Phase-2.5): pause rather than spin.
                return await self._gate.handle(plan_id, plan, uow)
            return await self._planning.handle(plan_id, plan, uow)

        # Unknown/unhandled phase — pause rather than silently spin.
        return Signal.PAUSED


async def advance_plan(
    plan_id: str,
    uow: UnitOfWork,
    runner: AgentRunner,
    agents: AgentRepository,
    workspace: Workspace,
    event_sink: AgentEventSink,
    clock: Clock,
    planning_handler: PhaseHandler | None = None,
    verifier: VerificationExecutor | None = None,
) -> str:
    """Backwards-compatible entry point. Builds a dispatcher and delegates. Returns
    the Signal's string value (so existing callers comparing to "continue"/"done"/
    etc. keep working). Pass `planning_handler` (the reasoner-driven
    PlanningHandler) so ARCHITECTURE/ENRICHING advance instead of pausing."""
    dispatcher = PlanDispatcher(
        runner,
        agents,
        workspace,
        event_sink,
        clock,
        planning_handler,
        verifier,
    )
    signal = await dispatcher.advance(plan_id, uow)
    return signal.value
