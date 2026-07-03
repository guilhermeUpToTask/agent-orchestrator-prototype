"""PlanningHandler — owns the WORKER-DRIVEN planning phases: ARCHITECTURE and
ENRICHING (the autonomous reasoner steps of the phase-machine driver,
roadmap 2.5).

The conversational phases (DISCOVERY, REPLANNING) do NOT belong to this
handler — they are chat/API-driven (the driver model): each user message
advances them via the conversation use cases, and the claim predicate makes
them invisible to workers. Reaching this handler in one of them is a defensive
anomaly and simply pauses.

Choreography per phase (same shape as the execution handler's crash safety):
the reasoner call — the LLM side effect — happens OUTSIDE any transaction; the
transaction then re-reads the plan, re-checks the phase (tolerant of a racing
human command), writes the new goal set, advances the phase, and commits state
+ PhaseAdvanced atomically through the outbox.

  ARCHITECTURE -> structure_goals -> set_iteration_goals -> ENRICHING (CONTINUE)
  ENRICHING    -> enrich_goals    -> set_iteration_goals + bind_agents
                                   -> AWAITING_REVIEW (PAUSED — gate ahead)

Agent binding happens at ENRICHING completion, when the task set is final;
tasks whose required_capabilities matched no agent fall back to the default and
emit AgentFellBackToDefault (a capability-coverage telemetry signal).
"""
from __future__ import annotations

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.events.outbox import AgentFellBackToDefault, PhaseAdvanced
from src.domain.repositories.agent_repo import AgentRepository

from src.app.handlers.base import Signal
from src.app.ports import Reasoner, UnitOfWork


class PlanningHandler:
    def __init__(self, reasoner: Reasoner, agents: AgentRepository) -> None:
        self._reasoner = reasoner
        self._agents = agents

    async def handle(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        if plan.phase == PlanPhase.ARCHITECTURE:
            return await self._architect(plan_id, plan, uow)
        if plan.phase == PlanPhase.ENRICHING:
            return await self._enrich(plan_id, plan, uow)
        # DISCOVERY / REPLANNING are conversational — never worker-driven.
        return Signal.PAUSED

    async def _architect(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        goals = await self._reasoner.structure_goals(plan)  # LLM, outside any txn
        with uow:
            plan = uow.plans.get(plan_id)
            if plan.phase != PlanPhase.ARCHITECTURE:
                return Signal.PAUSED  # raced by a human command; theirs wins
            plan.set_iteration_goals(goals)
            plan.advance_phase(PlanPhase.ENRICHING)
            plan.bump_version()
            uow.outbox.add(
                PhaseAdvanced(
                    plan_id=plan_id,
                    from_phase=PlanPhase.ARCHITECTURE.value,
                    to_phase=PlanPhase.ENRICHING.value,
                )
            )
            uow.plans.save(plan)
        return Signal.CONTINUE

    async def _enrich(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        goals = await self._reasoner.enrich_goals(plan)  # LLM, outside any txn
        agents = self._agents.list()
        default_id = self._agents.default_agent_id()
        with uow:
            plan = uow.plans.get(plan_id)
            if plan.phase != PlanPhase.ENRICHING:
                return Signal.PAUSED
            plan.set_iteration_goals(goals)
            fell_back = plan.bind_agents(agents, default_id)
            plan.advance_phase(PlanPhase.AWAITING_REVIEW)
            plan.bump_version()
            for task_id in fell_back:
                task = next(t for g in plan.goals for t in g.tasks if t.id == task_id)
                uow.outbox.add(
                    AgentFellBackToDefault(
                        plan_id=plan_id,
                        task_id=task_id,
                        required_capabilities=list(task.required_capabilities),
                    )
                )
            uow.outbox.add(
                PhaseAdvanced(
                    plan_id=plan_id,
                    from_phase=PlanPhase.ENRICHING.value,
                    to_phase=PlanPhase.AWAITING_REVIEW.value,
                )
            )
            uow.plans.save(plan)
        return Signal.PAUSED  # the pre-execution gate is next: release the plan
