from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.domain.aggregates.planner_orchestrator import Plan
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import (
    Cycle,
    CycleDraft,
    CycleStatus,
    GoalOutline,
    IntentProposal,
    OutputDisposition,
    PlanStatus,
    ProposalKind,
    ReviewGate,
    ReviewSubjectType,
)
from src.domain.errors.planning_errors import InvalidEditError

NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def gate(
    gate_id: str,
    subject_type: ReviewSubjectType,
    subject_id: str,
    revision: int,
    decisions: list[str] | None = None,
) -> ReviewGate:
    return ReviewGate(
        id=gate_id,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_revision=revision,
        allowed_decisions=decisions or ["approve", "request_changes", "cancel"],
        continuation="continue",
    )


def intent(
    plan: Plan,
    *,
    proposal_id: str = "intent-1",
    kind: ProposalKind = ProposalKind.INITIAL,
    source_cycle_id: str | None = None,
) -> IntentProposal:
    return IntentProposal(
        id=proposal_id,
        kind=kind,
        base_plan_version=plan.version,
        source_cycle_id=source_cycle_id,
        objective="ship a safe change",
        scope=["backend"],
        constraints=["sequential"],
        exclusions=["parallelism"],
    )


def draft(proposal: IntentProposal, *, draft_id: str = "draft-1") -> CycleDraft:
    return CycleDraft(
        id=draft_id,
        intent_proposal_id=proposal.id,
        base_plan_version=proposal.base_plan_version,
        source_cycle_id=proposal.source_cycle_id,
        goals=[
            GoalOutline(key="foundation", name="Foundation", objective="base", position=0),
            GoalOutline(
                key="delivery",
                name="Delivery",
                objective="finish",
                position=1,
                depends_on=["foundation"],
            ),
        ],
    )


def cycle(proposal: IntentProposal, item: CycleDraft, cycle_id: str = "cycle-1") -> Cycle:
    return Cycle(
        id=cycle_id,
        intent_proposal_id=proposal.id,
        draft_id=item.id,
        started_at=NOW,
        goals=[
            Goal(id="goal-1", name="Foundation", position=0, description="base"),
            Goal(
                id="goal-2",
                name="Delivery",
                position=1,
                description="finish",
                depends_on=["goal-1"],
            ),
        ],
    )


def test_initial_intent_draft_activation_and_completion_return_to_idle():
    plan = Plan(id="plan-1", project_id="project-1", brief="brief")
    proposal = intent(plan)
    plan.propose_intent(
        proposal,
        gate("gate-intent", ReviewSubjectType.INTENT, proposal.id, proposal.revision),
    )
    assert plan.status == PlanStatus.WAITING
    assert plan.activity == "review:intent"

    plan.approve_intent("gate-intent", 1, NOW)
    item = draft(proposal)
    plan.submit_cycle_draft(
        item,
        gate("gate-draft", ReviewSubjectType.CYCLE_DRAFT, item.id, item.revision),
    )
    plan.bump_version()
    active = cycle(proposal, item)
    plan.activate_cycle("gate-draft", 1, active, NOW)

    assert plan.status == PlanStatus.RUNNING
    assert plan.active_cycle is active
    assert len([c for c in plan.cycles if c.status == CycleStatus.ACTIVE]) == 1

    completion = gate(
        "gate-complete",
        ReviewSubjectType.CYCLE_COMPLETION,
        active.id,
        1,
        [item.value for item in OutputDisposition],
    )
    plan.open_completion_gate(completion, ["evidence://cycle-1"])
    plan.record_output_disposition(
        "gate-complete",
        1,
        OutputDisposition.RETAIN_BRANCH,
        "refs/heads/cycle/cycle-1",
        NOW,
    )

    assert plan.status == PlanStatus.IDLE
    assert plan.active_cycle is None
    assert active.status == CycleStatus.COMPLETED


def test_replan_activation_atomically_supersedes_active_cycle():
    plan = Plan(id="plan-1", project_id="project-1", brief="brief")
    initial = intent(plan)
    initial_draft = draft(initial)
    old = cycle(initial, initial_draft)
    plan.cycles = [old]
    plan.status = PlanStatus.PAUSED

    proposal = intent(
        plan,
        proposal_id="intent-2",
        kind=ProposalKind.REPLAN,
        source_cycle_id=old.id,
    )
    plan.propose_intent(
        proposal,
        gate("gate-intent-2", ReviewSubjectType.INTENT, proposal.id, 1),
    )
    plan.approve_intent("gate-intent-2", 1, NOW)
    replacement = draft(proposal, draft_id="draft-2")
    plan.submit_cycle_draft(
        replacement,
        gate("gate-draft-2", ReviewSubjectType.CYCLE_DRAFT, replacement.id, 1),
    )
    plan.bump_version()
    new_cycle = cycle(proposal, replacement, cycle_id="cycle-2")
    plan.activate_cycle("gate-draft-2", 1, new_cycle, NOW)

    assert old.status == CycleStatus.SUPERSEDED
    assert plan.active_cycle is new_cycle
    assert len([c for c in plan.cycles if c.status == CycleStatus.ACTIVE]) == 1


def test_unbound_and_stale_intents_are_rejected():
    unbound = Plan(id="legacy", brief="brief")
    with pytest.raises(InvalidEditError, match="project binding"):
        unbound.propose_intent(
            intent(unbound),
            gate("g", ReviewSubjectType.INTENT, "intent-1", 1),
        )

    plan = Plan(id="plan-1", project_id="project-1", brief="brief", version=3)
    stale = intent(plan)
    stale.base_plan_version = 2
    with pytest.raises(InvalidEditError, match="stale"):
        plan.propose_intent(
            stale,
            gate("g", ReviewSubjectType.INTENT, stale.id, 1),
        )


@pytest.mark.parametrize(
    "goals",
    [
        [
            GoalOutline(key="x", name="x", objective="x", position=0),
            GoalOutline(key="x", name="y", objective="y", position=1),
        ],
        [GoalOutline(key="x", name="x", objective="x", position=0, depends_on=["missing"])],
        [GoalOutline(key="x", name="x", objective="x", position=0, depends_on=["x"])],
        [
            GoalOutline(key="x", name="x", objective="x", position=0, depends_on=["y"]),
            GoalOutline(key="y", name="y", objective="y", position=1, depends_on=["x"]),
        ],
    ],
)
def test_cycle_draft_rejects_duplicate_unknown_self_and_cyclic_dependencies(goals):
    with pytest.raises(ValidationError):
        CycleDraft(
            id="draft",
            intent_proposal_id="intent",
            base_plan_version=0,
            goals=goals,
        )
