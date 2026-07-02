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

from domain.aggregates.planner_orchestrator import Plan, PlanPhase
from domain.repositories.agent_repo import AgentRepository

from application.handlers.base import PhaseHandler, Signal
from application.handlers.execution_handler import ExecutionHandler
from application.handlers.gate_handler import GateHandler
from application.ports import (
    AgentEventSink,
    AgentRunner,
    Clock,
    UnitOfWork,
    Workspace,
)

# Phase groups -> which handler owns them.
_PLANNING_PHASES = frozenset({
    PlanPhase.DISCOVERY, PlanPhase.ARCHITECTURE,
    PlanPhase.ENRICHING, PlanPhase.REPLANNING,
})
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
    ) -> None:
        self._execution = ExecutionHandler(runner, agents, workspace, event_sink, clock)
        self._gate = GateHandler()
        self._planning = planning_handler  # injected when the reasoner exists (Phase 2.5)

    async def advance(self, plan_id: str, uow: UnitOfWork) -> Signal:
        with uow:
            plan: Plan = uow.plans.get(plan_id)
            phase = plan.phase

        if phase in _TERMINAL_PHASES:
            return Signal.DONE if phase == PlanPhase.DONE else Signal.FAILED

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
) -> str:
    """Backwards-compatible entry point. Builds a dispatcher and delegates. Returns
    the Signal's string value (so existing callers comparing to "continue"/"done"/
    etc. keep working)."""
    dispatcher = PlanDispatcher(runner, agents, workspace, event_sink, clock)
    signal = await dispatcher.advance(plan_id, uow)
    return signal.value
