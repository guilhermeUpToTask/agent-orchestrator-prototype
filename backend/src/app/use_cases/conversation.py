"""conversation — the chat-driven phases (the driver model's third driver).

DISCOVERY and REPLANNING are advanced by USER MESSAGES, not by workers (the
claim predicate hides them from workers entirely). Each message is one reasoner
turn; when the turn produces a goal set, the plan flows into ARCHITECTURE and
the autonomous pipeline takes over.

  discovery_message  — DISCOVERY: brief (+ chat) -> draft goals -> ARCHITECTURE.
                       No prior context: this is iteration 1's cold start.
  replanning_message — REPLANNING: prior DONE results + chat -> new goal set ->
                       commit_replanned_goals (the ONE defined point where the
                       iteration increments and finalize-abandon closes what the
                       abandoned iteration left non-terminal) -> ARCHITECTURE.

The reasoner call happens OUTSIDE the transaction (LLM side effect); the
transaction re-reads, re-guards the phase, writes, and commits state +
PhaseAdvanced atomically.
"""
from __future__ import annotations

from src.domain.aggregates.planner_orchestrator import PlanPhase
from src.domain.errors.tasks_errors import InvalidTransitionError
from src.domain.events.outbox import PhaseAdvanced

from src.app.ports import Reasoner, UnitOfWork


async def discovery_message(
    plan_id: str, message: str, uow: UnitOfWork, reasoner: Reasoner
) -> None:
    with uow:
        plan = uow.plans.get(plan_id)
    if plan.phase != PlanPhase.DISCOVERY:
        raise InvalidTransitionError(
            "Plan", plan_id, plan.phase.value, PlanPhase.ARCHITECTURE.value
        )

    brief = f"{plan.brief}\n{message}".strip()
    goals = await reasoner.draft_goals(brief)  # LLM, outside any txn

    with uow:
        plan = uow.plans.get(plan_id)
        if plan.phase != PlanPhase.DISCOVERY:
            raise InvalidTransitionError(
                "Plan", plan_id, plan.phase.value, PlanPhase.ARCHITECTURE.value
            )
        plan.set_iteration_goals(goals)
        plan.advance_phase(PlanPhase.ARCHITECTURE)
        plan.bump_version()
        uow.outbox.add(
            PhaseAdvanced(
                plan_id=plan_id,
                from_phase=PlanPhase.DISCOVERY.value,
                to_phase=PlanPhase.ARCHITECTURE.value,
            )
        )
        uow.plans.save(plan)


async def replanning_message(
    plan_id: str, message: str, uow: UnitOfWork, reasoner: Reasoner
) -> None:
    with uow:
        plan = uow.plans.get(plan_id)
    if plan.phase != PlanPhase.REPLANNING:
        raise InvalidTransitionError(
            "Plan", plan_id, plan.phase.value, PlanPhase.ARCHITECTURE.value
        )

    # context-aware re-plan: the plan carries prior iterations' DONE results
    goals = await reasoner.replan_goals(plan, message)  # LLM, outside any txn

    with uow:
        plan = uow.plans.get(plan_id)
        # commit_replanned_goals guards REPLANNING itself: finalize-abandon of
        # leftover non-terminal work, append-only goal addition, iteration += 1
        plan.commit_replanned_goals(goals)
        plan.bump_version()
        uow.outbox.add(
            PhaseAdvanced(
                plan_id=plan_id,
                from_phase=PlanPhase.REPLANNING.value,
                to_phase=PlanPhase.ARCHITECTURE.value,
            )
        )
        uow.plans.save(plan)
