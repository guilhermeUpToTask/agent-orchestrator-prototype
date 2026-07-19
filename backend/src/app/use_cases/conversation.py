"""Synchronous, durable intent-discovery turns.

The submitted user message is persisted before the reasoner call. Operational
status lives in the execution ledger, while an accepted IntentProposal and its
review gate commit with domain events in the Plan transaction.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from pydantic import BaseModel

from src.app.execution_records import (
    PlanningOperation,
    PlanningOperationStatus,
)
from src.app.ports import (
    ChatMessage,
    ChatStore,
    Clock,
    ConversationMode,
    Reasoner,
    ReasonerUnavailable,
    UnitOfWork,
)
from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.planning_artifacts import (
    IntentProposal,
    PlanStatus,
    ProposalKind,
    ReviewGate,
    ReviewSubjectType,
)
from src.domain.errors.planning_errors import InvalidEditError
from src.domain.events.outbox import IntentProposed, ReviewGateOpened
from src.domain.factories.identity import new_id
from src.domain.ports.reasoner_port import IntentCandidate, ReasonerReply


class ConversationResult(BaseModel):
    reply: str
    committed: bool
    phase: PlanPhase
    operation_id: str
    operation_status: PlanningOperationStatus
    error: str | None = None


async def discovery_message(
    plan_id: str,
    message: str,
    uow: UnitOfWork,
    reasoner: Reasoner,
    chat: ChatStore,
    clock: Clock,
) -> ConversationResult:
    return await _conversation_turn(
        plan_id, message, uow, reasoner, chat, clock, ProposalKind.INITIAL
    )


async def replanning_message(
    plan_id: str,
    message: str,
    uow: UnitOfWork,
    reasoner: Reasoner,
    chat: ChatStore,
    clock: Clock,
) -> ConversationResult:
    return await _conversation_turn(
        plan_id, message, uow, reasoner, chat, clock, ProposalKind.REPLAN
    )


def _start_operation(
    plan_id: str,
    kind: ProposalKind,
    uow: UnitOfWork,
    clock: Clock,
) -> tuple[Plan, PlanningOperation]:
    now = clock.now()
    with uow:
        plan = uow.plans.get(plan_id)
        if plan.project_id is None:
            raise InvalidEditError("project binding is required before intent discovery")
        if plan.intent_proposal is not None or (
            plan.review_gate is not None and plan.review_gate.unresolved
        ):
            raise InvalidEditError("finish or cancel the current planning review first")
        if kind == ProposalKind.REPLAN and plan.active_cycle is None:
            raise InvalidEditError("replan discovery requires an active cycle")
        if kind == ProposalKind.INITIAL and plan.active_cycle is not None:
            raise InvalidEditError("an active cycle requires replan discovery")
        if kind == ProposalKind.INITIAL and plan.status not in {
            PlanStatus.IDLE,
            PlanStatus.WAITING,
        }:
            raise InvalidEditError("initial discovery is not legal in the current state")
        if kind == ProposalKind.REPLAN and plan.status not in {
            PlanStatus.PAUSED,
            PlanStatus.BLOCKED,
            PlanStatus.IDLE,
        }:
            cycle = plan.active_cycle
            settled = cycle is not None and all(
                task.is_terminal for goal in cycle.goals for task in goal.tasks
            )
            if not (plan.status == PlanStatus.WAITING and settled):
                raise InvalidEditError("replan discovery requires settled active-cycle work")

        purpose = "intent_discovery" if kind == ProposalKind.INITIAL else "replan_discovery"
        operation = uow.executions.find_active_planning_operation(plan_id, purpose)
        if operation is None:
            operation = PlanningOperation(
                id=str(uuid4()),
                plan_id=plan_id,
                purpose=purpose,
                status=PlanningOperationStatus.STARTED,
                created_at=now,
                updated_at=now,
                started_at=now,
                last_liveness_at=now,
            )
            uow.executions.add_planning_operation(operation)
        else:
            operation = replace(
                operation,
                status=PlanningOperationStatus.STARTED,
                updated_at=now,
                started_at=operation.started_at or now,
                last_liveness_at=now,
                completed_at=None,
                failure_kind=None,
                safe_message=None,
            )
            uow.executions.update_planning_operation(operation)
        return plan, operation


def _candidate_from_legacy(reply: ReasonerReply, brief: str) -> IntentCandidate | None:
    """Temporary adapter for scripted Reasoners while clients migrate.

    Legacy goal DTOs are collapsed into intent text; they are never persisted as
    executable goals, so roadmap generation remains post-approval and canonical.
    """
    if reply.intent is not None:
        return reply.intent
    if not reply.goals:
        return None
    return IntentCandidate(
        normalized_brief=brief,
        objective="\n".join(f"{goal.name}: {goal.description}".strip() for goal in reply.goals),
    )


async def _conversation_turn(
    plan_id: str,
    message: str,
    uow: UnitOfWork,
    reasoner: Reasoner,
    chat: ChatStore,
    clock: Clock,
    kind: ProposalKind,
) -> ConversationResult:
    plan, operation = _start_operation(plan_id, kind, uow, clock)
    history = chat.list(plan_id)
    chat.append(
        plan_id,
        ChatMessage(
            role="user",
            content=message,
            created_at=clock.now(),
            meta={"planning_operation_id": operation.id, "submitted_brief": True},
        ),
    )

    mode: ConversationMode = "discovery" if kind == ProposalKind.INITIAL else "replanning"
    try:
        reply = await reasoner.converse(plan, history, message, mode)
    except ReasonerUnavailable as exc:
        now = clock.now()
        failed = replace(
            operation,
            status=PlanningOperationStatus.FAILED,
            updated_at=now,
            completed_at=now,
            last_liveness_at=now,
            failure_kind="reasoner_unavailable",
            safe_message=exc.reason[:500],
        )
        with uow:
            uow.executions.update_planning_operation(failed)
        assistant = f"Discovery failed before an intent proposal was committed. {exc.reason[:500]}"
        chat.append(
            plan_id,
            ChatMessage(
                role="assistant",
                content=assistant,
                created_at=now,
                meta={
                    "committed": False,
                    "planning_operation_id": operation.id,
                    "planning_status": PlanningOperationStatus.FAILED.value,
                },
            ),
        )
        return ConversationResult(
            reply=assistant,
            committed=False,
            phase=plan.phase,
            operation_id=operation.id,
            operation_status=PlanningOperationStatus.FAILED,
            error=exc.reason[:500],
        )
    except Exception as exc:
        now = clock.now()
        with uow:
            uow.executions.update_planning_operation(
                replace(
                    operation,
                    status=PlanningOperationStatus.FAILED,
                    updated_at=now,
                    completed_at=now,
                    last_liveness_at=now,
                    failure_kind="reasoner_crash",
                    safe_message=str(exc)[:500],
                )
            )
        raise

    candidate = _candidate_from_legacy(reply, plan.brief)
    now = clock.now()
    if candidate is None:
        waiting = replace(
            operation,
            status=PlanningOperationStatus.WAITING_FOR_USER,
            updated_at=now,
            last_liveness_at=now,
            model_request_count=(operation.model_request_count + reply.model_request_count),
            tool_turn_count=operation.tool_turn_count + reply.tool_turn_count,
        )
        with uow:
            uow.executions.update_planning_operation(waiting)
        chat.append(
            plan_id,
            ChatMessage(
                role="assistant",
                content=reply.message,
                created_at=now,
                meta={
                    "committed": False,
                    "planning_operation_id": operation.id,
                    "planning_status": PlanningOperationStatus.WAITING_FOR_USER.value,
                },
            ),
        )
        return ConversationResult(
            reply=reply.message,
            committed=False,
            phase=plan.phase,
            operation_id=operation.id,
            operation_status=PlanningOperationStatus.WAITING_FOR_USER,
        )

    with uow:
        fresh = uow.plans.get(plan_id)
        proposal = IntentProposal(
            id=new_id(),
            kind=kind,
            base_plan_version=fresh.version,
            source_cycle_id=(
                fresh.active_cycle.id
                if kind == ProposalKind.REPLAN and fresh.active_cycle is not None
                else None
            ),
            objective=candidate.objective,
            scope=list(candidate.scope),
            constraints=list(candidate.constraints),
            exclusions=list(candidate.exclusions),
            planner_session_ref=operation.id,
        )
        gate = ReviewGate(
            id=new_id(),
            subject_type=ReviewSubjectType.INTENT,
            subject_id=proposal.id,
            subject_revision=proposal.revision,
            allowed_decisions=["approve", "edit", "cancel"],
            continuation="Approve the exact intent revision before roadmap generation.",
        )
        fresh.propose_intent(proposal, gate)
        fresh.bump_version()
        uow.outbox.add(
            IntentProposed(
                plan_id=fresh.id,
                proposal_id=proposal.id,
                revision=proposal.revision,
            )
        )
        uow.outbox.add(
            ReviewGateOpened(
                plan_id=fresh.id,
                gate_id=gate.id,
                subject_type=gate.subject_type.value,
                subject_id=proposal.id,
                subject_revision=proposal.revision,
            )
        )
        committed = replace(
            operation,
            status=PlanningOperationStatus.COMMITTED,
            updated_at=now,
            completed_at=now,
            last_liveness_at=now,
            model_request_count=(operation.model_request_count + reply.model_request_count),
            tool_turn_count=operation.tool_turn_count + reply.tool_turn_count,
        )
        uow.executions.update_planning_operation(committed)
        uow.plans.save(fresh)

    chat.append(
        plan_id,
        ChatMessage(
            role="assistant",
            content=reply.message,
            created_at=now,
            meta={
                "committed": True,
                "planning_operation_id": operation.id,
                "planning_status": PlanningOperationStatus.COMMITTED.value,
                "normalized_brief": candidate.normalized_brief,
                "assumptions": candidate.assumptions,
                "unresolved_questions": candidate.unresolved_questions,
            },
        ),
    )
    return ConversationResult(
        reply=reply.message,
        committed=True,
        phase=plan.phase,
        operation_id=operation.id,
        operation_status=PlanningOperationStatus.COMMITTED,
    )
