"""PlanningHandler — owns the reasoner-driven phases: DISCOVERY, ARCHITECTURE,
ENRICHING, REPLANNING.

SEAM / NOT YET IMPLEMENTED (Phase 2.5). This is where the Reasoner adapter is
called to produce/structure/enrich/re-plan goals, then advance the phase. It is a
separate handler precisely so this LLM-planning logic never mixes into task
execution. Wiring it is the "phase-machine driver" work in the roadmap.

Structure it will take when implemented:
    with uow:
        plan = uow.plans.get(plan_id)
        match plan.phase:
            DISCOVERY   -> goals = reasoner.draft_plan(plan.brief); attach; -> ARCHITECTURE
            ARCHITECTURE-> reasoner.structure(...); -> ENRICHING
            ENRICHING   -> reasoner.enrich(...); -> AWAITING_REVIEW
            REPLANNING  -> reasoner.replan(prior_results, chat); append goals; -> ARCHITECTURE
        bump_version; emit PhaseAdvanced; save
    return Signal.CONTINUE
"""
from __future__ import annotations

from domain.aggregates.planner_orchestrator import Plan

from application.handlers.base import Signal
from application.ports import Reasoner, UnitOfWork


class PlanningHandler:
    def __init__(self, reasoner: Reasoner) -> None:
        self._reasoner = reasoner

    async def handle(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        raise NotImplementedError(
            "PlanningHandler is a Phase 2.5 seam: wire the Reasoner into the "
            "DISCOVERY/ARCHITECTURE/ENRICHING/REPLANNING phases here."
        )
