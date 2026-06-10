"""
src/api/routers/plan.py — Plan lifecycle endpoints.

Covers:
  GET  /plan                  read the current plan
  GET  /plan/history          plan history log
  POST /plan/approve-brief    operator approves the discovery brief
  POST /plan/approve-architecture  operator selects architecture decisions
  POST /plan/approve-phase    operator greenlights the next phase

State machine (enforced by the ProjectPlan aggregate — violations → 409):

  discovery → architecture → phase_active → phase_review → phase_active …
                                                         → done

Two distinct approval moments exist and must not be conflated:
  * Plan DECISION (mid-phase): /plan/approve-architecture applies the
    planner's pending architecture decisions while the plan is in
    `architecture`, dispatching the first phase's goals.
  * Plan REVIEW (end-of-phase): /plan/approve-phase acts on a completed
    PHASE_REVIEW session while the plan is in `phase_review`, releasing
    the next phase (or marking the plan `done`).

Every 409 carries a PlanConflictResponse body with `action`,
`current_status`, and `expected_status` so clients can recover.
"""
from __future__ import annotations

from fastapi import APIRouter, status

from src.api.dependencies import (
    PlanOrchestratorDep,
    ProjectPlanRepoDep,
)
from src.api.schemas.common import ErrorResponse, PlanConflictResponse
from src.api.schemas.plan import (
    ApproveBriefResponse,
    ApproveArchitectureRequest,
    ApproveArchitectureResponse,
    ApprovePhaseRequest,
    ApprovePhaseResponse,
    PlanBriefResponse,
    PlanHistoryEntryResponse,
    PlanPhaseResponse,
    PlanResponse,
)
from src.api.sse import publish_sse

router = APIRouter(prefix="/plan", tags=["plan"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _plan_to_response(plan) -> PlanResponse:
    phases = [
        PlanPhaseResponse(
            index=p.index,
            name=p.name,
            goal=p.goal,
            goal_names=p.goal_names,
            status=p.status.value,
            exit_criteria=p.exit_criteria,
            lessons=p.lessons,
        )
        for p in plan.phases
    ]
    brief = (
        PlanBriefResponse(
            vision=plan.brief.vision,
            constraints=plan.brief.constraints,
            phase_1_exit_criteria=plan.brief.phase_1_exit_criteria,
            open_questions=plan.brief.open_questions,
        )
        if plan.brief
        else None
    )
    history = [PlanHistoryEntryResponse(**h.model_dump()) for h in plan.history]
    return PlanResponse(
        plan_id=plan.plan_id,
        status=plan.status.value,
        vision=plan.vision,
        architecture_summary=plan.architecture_summary,
        current_phase_index=plan.current_phase_index,
        state_version=plan.state_version,
        phases=phases,
        brief=brief,
        history=history,
    )


# ── Read ──────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=PlanResponse,
    summary="Get Current Plan",
    description=(
        "Returns the full plan read-model including phases, brief, and history. "
        "Returns an empty discovery-stage plan when no plan file exists yet."
    ),
)
def get_plan(repo: ProjectPlanRepoDep) -> PlanResponse:
    try:
        plan = repo.load()
    except KeyError:
        # No plan file yet — present an empty discovery-stage plan.
        return PlanResponse(
            plan_id=None,
            status="discovery",
            vision="",
            current_phase_index=0,
            state_version=0,
            phases=[],
            brief=None,
            history=[],
        )
    return _plan_to_response(plan)


@router.get(
    "/history",
    response_model=list[PlanHistoryEntryResponse],
    summary="Get Plan History",
    description="Returns the ordered history log of all plan state transitions.",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No plan exists yet.",
        }
    },
)
def get_plan_history(repo: ProjectPlanRepoDep) -> list[PlanHistoryEntryResponse]:
    try:
        plan = repo.load()
    except KeyError:
        return []
    return [PlanHistoryEntryResponse(**h.model_dump()) for h in plan.history]


# ── Approve Brief ─────────────────────────────────────────────────────────────

@router.post(
    "/approve-brief",
    response_model=ApproveBriefResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve Discovery Brief",
    description=(
        "Operator approves the discovery brief, advancing the plan from "
        "`discovery` to `architecture` status."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "model": PlanConflictResponse,
            "description": (
                "Plan is not in `discovery` status. The body reports the "
                "current vs expected status."
            ),
        }
    },
)
def approve_brief(orchestrator: PlanOrchestratorDep) -> ApproveBriefResponse:
    plan = orchestrator.approve_brief()
    publish_sse("plan.status_changed", {"status": plan.status.value})
    return ApproveBriefResponse(plan_status=plan.status.value, vision=plan.vision)


# ── Approve Architecture ──────────────────────────────────────────────────────

@router.post(
    "/approve-architecture",
    response_model=ApproveArchitectureResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve Architecture Decisions",
    description=(
        "**Mid-phase plan decision.** Operator selects which architecture "
        "decision IDs to apply while the plan is in `architecture` status, "
        "triggering goal dispatch for the first phase."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "model": PlanConflictResponse,
            "description": (
                "Plan is not in `architecture` status (e.g. already in "
                "`phase_review`). The body reports the current vs expected status."
            ),
        }
    },
)
def approve_architecture(
    payload: ApproveArchitectureRequest,
    orchestrator: PlanOrchestratorDep,
) -> ApproveArchitectureResponse:
    result = orchestrator.approve_architecture(decision_ids=payload.decision_ids)
    publish_sse("plan.status_changed", {"status": result.plan_status})
    for goal_id in result.goals_dispatched:
        publish_sse("goal.dispatched", {"goal_id": goal_id})
    return ApproveArchitectureResponse(
        decisions_applied=result.decisions_applied,
        goals_dispatched=result.goals_dispatched,
        plan_status=result.plan_status,
    )


# ── Approve Phase ─────────────────────────────────────────────────────────────

@router.post(
    "/approve-phase",
    response_model=ApprovePhaseResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve Phase Review",
    description=(
        "**End-of-phase plan review.** Operator greenlights the phase review "
        "while the plan is in `phase_review` status, releasing goals for the "
        "next phase. Set `approve_next=false` to finish and mark the plan `done`."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "model": PlanConflictResponse,
            "description": (
                "Plan is not in `phase_review` status. The body reports the "
                "current vs expected status."
            ),
        }
    },
)
def approve_phase(
    payload: ApprovePhaseRequest,
    orchestrator: PlanOrchestratorDep,
) -> ApprovePhaseResponse:
    result = orchestrator.approve_phase_review(approve_next=payload.approve_next)
    publish_sse("plan.status_changed", {"status": result.plan_status})
    return ApprovePhaseResponse(
        decisions_applied=result.decisions_applied,
        goals_dispatched=result.goals_dispatched,
        plan_status=result.plan_status,
    )
