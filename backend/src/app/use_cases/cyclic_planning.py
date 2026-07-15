"""Application transactions for versioned intent, cycle, review, and publication."""

from __future__ import annotations

from src.app.ports import Clock, UnitOfWork
from src.domain.aggregates.planner_orchestrator import PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import (
    Cycle,
    CycleDraft,
    GoalOutline,
    IntentProposal,
    OutputDisposition,
    ProposalKind,
    ReviewGate,
    ReviewSubjectType,
)
from src.domain.events.outbox import (
    CycleActivated,
    CycleDrafted,
    IntentApproved,
    IntentProposed,
    OutputDispositionRecorded,
    ReviewGateOpened,
)
from src.domain.factories.identity import new_id
from src.domain.errors.planning_errors import InvalidEditError


def propose_intent(
    plan_id: str,
    *,
    objective: str,
    scope: list[str],
    constraints: list[str],
    exclusions: list[str],
    kind: ProposalKind,
    planner_session_ref: str | None,
    uow: UnitOfWork,
    clock: Clock,
) -> IntentProposal:
    with uow:
        plan = uow.plans.get(plan_id)
        source_cycle_id = (
            plan.active_cycle.id if kind == ProposalKind.REPLAN and plan.active_cycle else None
        )
        proposal = IntentProposal(
            id=new_id(),
            kind=kind,
            base_plan_version=plan.version,
            source_cycle_id=source_cycle_id,
            objective=objective,
            scope=scope,
            constraints=constraints,
            exclusions=exclusions,
            planner_session_ref=planner_session_ref,
        )
        gate = ReviewGate(
            id=new_id(),
            subject_type=ReviewSubjectType.INTENT,
            subject_id=proposal.id,
            subject_revision=proposal.revision,
            allowed_decisions=["approve", "edit", "cancel"],
            continuation="Approve the exact intent revision before cycle architecture.",
        )
        plan.propose_intent(proposal, gate)
        plan.bump_version()
        uow.outbox.add(IntentProposed(plan_id=plan.id, proposal_id=proposal.id, revision=1))
        uow.outbox.add(
            ReviewGateOpened(
                plan_id=plan.id,
                gate_id=gate.id,
                subject_type=gate.subject_type.value,
                subject_id=proposal.id,
                subject_revision=1,
            )
        )
        uow.plans.save(plan)
        return proposal


def approve_intent(
    plan_id: str,
    gate_id: str,
    revision: int,
    uow: UnitOfWork,
    clock: Clock,
) -> None:
    with uow:
        plan = uow.plans.get(plan_id)
        proposal = plan.intent_proposal
        plan.approve_intent(gate_id, revision, clock.now())
        plan.bump_version()
        assert proposal is not None
        uow.outbox.add(IntentApproved(plan_id=plan.id, proposal_id=proposal.id, revision=revision))
        uow.plans.save(plan)


def revise_intent(
    plan_id: str,
    *,
    objective: str,
    scope: list[str],
    constraints: list[str],
    exclusions: list[str],
    planner_session_ref: str | None,
    uow: UnitOfWork,
    clock: Clock,
) -> IntentProposal:
    with uow:
        plan = uow.plans.get(plan_id)
        current = plan.intent_proposal
        if current is None:
            raise InvalidEditError("intent proposal not found")
        proposal = current.model_copy(
            update={
                "base_plan_version": plan.version,
                "objective": objective,
                "scope": scope,
                "constraints": constraints,
                "exclusions": exclusions,
                "revision": current.revision + 1,
                "planner_session_ref": planner_session_ref,
                "approved_at": None,
                "cancelled_at": None,
            }
        )
        gate = ReviewGate(
            id=new_id(),
            subject_type=ReviewSubjectType.INTENT,
            subject_id=proposal.id,
            subject_revision=proposal.revision,
            allowed_decisions=["approve", "edit", "cancel"],
            continuation="Approve the revised intent before cycle architecture.",
        )
        plan.revise_intent(proposal, gate, clock.now())
        plan.bump_version()
        uow.outbox.add(
            IntentProposed(
                plan_id=plan.id,
                proposal_id=proposal.id,
                revision=proposal.revision,
            )
        )
        uow.outbox.add(
            ReviewGateOpened(
                plan_id=plan.id,
                gate_id=gate.id,
                subject_type=gate.subject_type.value,
                subject_id=proposal.id,
                subject_revision=proposal.revision,
            )
        )
        uow.plans.save(plan)
        return proposal


def cancel_intent(
    plan_id: str,
    *,
    uow: UnitOfWork,
    clock: Clock,
) -> None:
    with uow:
        plan = uow.plans.get(plan_id)
        plan.cancel_intent(clock.now())
        plan.bump_version()
        uow.plans.save(plan)


def submit_cycle_draft(
    plan_id: str,
    *,
    goals: list[GoalOutline],
    unfinished_source_treatment: str | None,
    uow: UnitOfWork,
) -> CycleDraft:
    with uow:
        plan = uow.plans.get(plan_id)
        proposal = plan.intent_proposal
        if proposal is None:
            raise InvalidEditError("approved intent is required")
        draft = CycleDraft(
            id=new_id(),
            intent_proposal_id=proposal.id,
            base_plan_version=plan.version,
            source_cycle_id=proposal.source_cycle_id,
            goals=goals,
            unfinished_source_treatment=unfinished_source_treatment,
        )
        gate = ReviewGate(
            id=new_id(),
            subject_type=ReviewSubjectType.CYCLE_DRAFT,
            subject_id=draft.id,
            subject_revision=draft.revision,
            allowed_decisions=["approve", "edit", "cancel"],
            continuation="Approve the exact CycleDraft revision to activate execution.",
        )
        plan.submit_cycle_draft(draft, gate)
        plan.bump_version()
        uow.outbox.add(CycleDrafted(plan_id=plan.id, draft_id=draft.id, revision=1))
        uow.outbox.add(
            ReviewGateOpened(
                plan_id=plan.id,
                gate_id=gate.id,
                subject_type=gate.subject_type.value,
                subject_id=draft.id,
                subject_revision=1,
            )
        )
        uow.plans.save(plan)
        return draft


def revise_cycle_draft(
    plan_id: str,
    *,
    goals: list[GoalOutline],
    unfinished_source_treatment: str | None,
    uow: UnitOfWork,
    clock: Clock,
) -> CycleDraft:
    with uow:
        plan = uow.plans.get(plan_id)
        current = plan.cycle_draft
        if current is None:
            raise InvalidEditError("cycle draft not found")
        draft = current.model_copy(
            update={
                "base_plan_version": plan.version,
                "goals": goals,
                "revision": current.revision + 1,
                "unfinished_source_treatment": unfinished_source_treatment,
                "approved_at": None,
                "cancelled_at": None,
            }
        )
        # model_copy does not re-run Pydantic validators.
        draft = CycleDraft.model_validate(draft.model_dump())
        gate = ReviewGate(
            id=new_id(),
            subject_type=ReviewSubjectType.CYCLE_DRAFT,
            subject_id=draft.id,
            subject_revision=draft.revision,
            allowed_decisions=["approve", "edit", "cancel"],
            continuation="Approve the revised CycleDraft to activate execution.",
        )
        plan.revise_cycle_draft(draft, gate, clock.now())
        plan.bump_version()
        uow.outbox.add(
            CycleDrafted(plan_id=plan.id, draft_id=draft.id, revision=draft.revision)
        )
        uow.outbox.add(
            ReviewGateOpened(
                plan_id=plan.id,
                gate_id=gate.id,
                subject_type=gate.subject_type.value,
                subject_id=draft.id,
                subject_revision=draft.revision,
            )
        )
        uow.plans.save(plan)
        return draft


def cancel_cycle_draft(
    plan_id: str,
    *,
    uow: UnitOfWork,
    clock: Clock,
) -> None:
    with uow:
        plan = uow.plans.get(plan_id)
        plan.cancel_cycle_draft(clock.now())
        plan.bump_version()
        uow.plans.save(plan)


def activate_cycle(
    plan_id: str,
    gate_id: str,
    revision: int,
    uow: UnitOfWork,
    clock: Clock,
) -> Cycle:
    with uow:
        plan = uow.plans.get(plan_id)
        draft = plan.cycle_draft
        if draft is None:
            raise InvalidEditError("cycle draft is required")
        goal_ids = {outline.key: new_id() for outline in draft.goals}
        goals = [
            Goal(
                id=goal_ids[outline.key],
                name=outline.name,
                position=outline.position,
                description=outline.objective,
                depends_on=[goal_ids[key] for key in outline.depends_on],
            )
            for outline in sorted(draft.goals, key=lambda item: item.position)
        ]
        cycle = Cycle(
            id=new_id(),
            intent_proposal_id=draft.intent_proposal_id,
            draft_id=draft.id,
            goals=goals,
            started_at=clock.now(),
        )
        plan.activate_cycle(gate_id, revision, cycle, clock.now())
        # Compatibility projection only; cyclic navigation/status are authoritative.
        plan._set_phase(PlanPhase.ENRICHING)
        plan.bump_version()
        uow.outbox.add(
            CycleActivated(
                plan_id=plan.id,
                cycle_id=cycle.id,
                draft_id=draft.id,
            )
        )
        uow.plans.save(plan)
        return cycle


def record_output_disposition(
    plan_id: str,
    gate_id: str,
    revision: int,
    disposition: OutputDisposition,
    output_reference: str | None,
    uow: UnitOfWork,
    clock: Clock,
) -> None:
    with uow:
        plan = uow.plans.get(plan_id)
        cycle = plan.active_cycle
        plan.record_output_disposition(
            gate_id,
            revision,
            disposition,
            output_reference,
            clock.now(),
        )
        plan.bump_version()
        assert cycle is not None
        uow.outbox.add(
            OutputDispositionRecorded(
                plan_id=plan.id,
                cycle_id=cycle.id,
                disposition=disposition.value,
                output_reference=output_reference,
            )
        )
        uow.plans.save(plan)
