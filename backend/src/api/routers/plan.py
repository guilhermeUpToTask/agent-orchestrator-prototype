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

import threading

import structlog
from fastapi import APIRouter, HTTPException, status

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
    ArchitectureStatusResponse,
    GoalDispatchFailureResponse,
    PlanBriefResponse,
    PlanHistoryEntryResponse,
    PlanPhaseResponse,
    PlanResponse,
    ResumeDispatchResponse,
)
from src.api.schemas.sessions import SessionAccepted
from src.api.sessions import ApiSession, registry
from src.api.sse import publish_sse
from src.domain.aggregates.project_plan import ProjectPlanStatus

log = structlog.get_logger(__name__)

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


def _publish_goal_dispatch_failures(failures) -> None:
    """Surface partial-dispatch failures to the operator over SSE.

    Goal dispatch happens after the plan transition is already committed, so a
    failed goal can't fail the HTTP request — without this, a goal that never
    got a branch would vanish silently. The frontend renders these as errors.
    """
    for failure in failures:
        log.warning(
            "plan.goal_dispatch_failed",
            goal_name=failure.goal_name,
            error=failure.error,
        )
        publish_sse(
            "goal.dispatch_failed",
            {"goal_name": failure.goal_name, "error": failure.error},
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
    # Auto-start architecture drafting — the approval's stated consequence.
    # Best-effort: a launch failure must not fail the (already-applied) approval.
    if (
        plan.status == ProjectPlanStatus.ARCHITECTURE
        and registry.active("architecture") is None
    ):
        try:
            _launch_architecture_session(orchestrator)
        except Exception as exc:
            log.warning("plan.architecture_autostart_failed", error=str(exc))
    return ApproveBriefResponse(plan_status=plan.status.value, vision=plan.vision)


# ── Run Architecture (session) ────────────────────────────────────────────────

def _launch_architecture_session(orchestrator) -> ApiSession:
    """Create + start the autonomous architecture session on a daemon thread.

    Shared by ``POST /plan/architecture/run`` (manual retry) and
    ``POST /plan/approve-brief`` (auto-start), so drafting begins the moment
    the plan enters ``architecture`` — fulfilling the approval copy and
    removing the dead "press Draft, nothing happens" path. Callers guard
    against a concurrent run via ``registry.active("architecture")``.
    """
    session = registry.create("architecture")

    def run() -> None:
        try:
            result = orchestrator.run_architecture(
                cancel_check=lambda: session.cancel_requested
            )
            if result.failure_reason:
                session.fail(result.failure_reason)
            else:
                roadmap = getattr(result, "roadmap", None)
                goal_specs = roadmap.goal_specs if roadmap is not None else {}
                session.complete(
                    {
                        "decisions": [
                            {"id": d.id, "domain": d.domain, "feature_tag": d.feature_tag}
                            for d in result.pending_decisions
                        ],
                        "phases": [
                            {
                                "index": p.index,
                                "name": p.name,
                                "goal_names": p.goal_names,
                                "goals": [
                                    {
                                        "name": name,
                                        "description": (
                                            goal_specs[name].description
                                            if name in goal_specs
                                            else ""
                                        ),
                                    }
                                    for name in p.goal_names
                                ],
                            }
                            for p in result.pending_phases
                        ],
                    }
                )
        except Exception as exc:
            log.exception(
                "architecture.session_failed",
                session_id=session.session_id,
                error=str(exc),
            )
            session.fail(str(exc))
        finally:
            if session.status == "done":
                publish_sse(
                    "plan.architecture_completed",
                    {"session_id": session.session_id},
                )
            else:
                publish_sse(
                    "plan.architecture_failed",
                    {"session_id": session.session_id, "error": session.error},
                )

    threading.Thread(
        target=run, daemon=True, name=f"architecture-{session.session_id}"
    ).start()

    return session


@router.post(
    "/architecture/run",
    response_model=SessionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run Architecture Session",
    description=(
        "Launches the autonomous architecture planner in a background thread "
        "while the plan is in `architecture` status. Normally drafting "
        "auto-starts on `POST /plan/approve-brief`; this endpoint is the manual "
        "retry. The planner drafts the phase plan and decisions; each is "
        "published over SSE as `plan.decision_proposed` / `plan.phase_proposed`, "
        "and the session emits `plan.architecture_completed` / "
        "`plan.architecture_failed` on termination. Poll "
        "`GET /plan/architecture/status` for reload-safe readiness."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "An architecture session is already in progress.",
        }
    },
)
async def run_architecture(orchestrator: PlanOrchestratorDep) -> SessionAccepted:
    if registry.active("architecture") is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An architecture session is already in progress.",
        )
    session = _launch_architecture_session(orchestrator)
    return SessionAccepted(session_id=session.session_id, status=session.status)


@router.get(
    "/architecture/status",
    response_model=ArchitectureStatusResponse,
    summary="Architecture Session Status",
    description=(
        "Reload-resilient readiness of the autonomous architecture run. "
        "`state` is `running` while drafting, `completed` once a usable roadmap "
        "is ready to approve (decisions/phases included), `failed` if the run "
        "ended without one, or `none` if no run has started in this process."
    ),
)
def architecture_status() -> ArchitectureStatusResponse:
    session = registry.latest("architecture")
    if session is None:
        return ArchitectureStatusResponse(state="none")
    state_map = {
        "running": "running",
        "waiting_input": "running",
        "done": "completed",
        "failed": "failed",
    }
    result = session.result or {}
    return ArchitectureStatusResponse(
        state=state_map.get(session.status, "running"),
        session_id=session.session_id,
        decisions=result.get("decisions", []),
        phases=result.get("phases", []),
        error=session.error,
    )


@router.post(
    "/architecture/cancel",
    response_model=SessionAccepted,
    summary="Cancel Architecture Session",
    description=(
        "Requests a cooperative stop of the in-progress architecture session. "
        "The planner loop checks the flag between turns, so cancellation takes "
        "effect after the current turn finishes. Any decisions and phases already "
        "proposed are auto-finalized to the approval gate; if nothing usable was "
        "produced the session ends as `plan.architecture_failed`."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No architecture session is currently running.",
        }
    },
)
async def cancel_architecture() -> SessionAccepted:
    session = registry.active("architecture")
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No architecture session is currently running.",
        )
    session.request_cancel()
    return SessionAccepted(session_id=session.session_id, status=session.status)


# ── Run Phase Review (session) ────────────────────────────────────────────────

@router.post(
    "/phase-review/run",
    response_model=SessionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run Phase Review Session",
    description=(
        "Launches the autonomous phase-review planner in a background thread "
        "while the plan is in `phase_review` status. The planner records lessons "
        "and proposes the next phase; the session emits "
        "`plan.phase_review_completed` / `plan.phase_review_failed` on "
        "termination. The operator then calls `POST /plan/approve-phase`."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "A phase-review session is already in progress.",
        }
    },
)
async def run_phase_review(orchestrator: PlanOrchestratorDep) -> SessionAccepted:
    if registry.active("phase_review") is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A phase-review session is already in progress.",
        )

    session = registry.create("phase_review")

    def run() -> None:
        try:
            result = orchestrator.run_phase_review()
            if result.failure_reason:
                session.fail(result.failure_reason)
            else:
                next_phase = result.next_phase_proposal
                session.complete(
                    {
                        "lessons": result.lessons,
                        "next_phase": (
                            {"index": next_phase.index, "name": next_phase.name}
                            if next_phase
                            else None
                        ),
                        "decisions": [
                            {"id": d.id, "domain": d.domain}
                            for d in result.pending_decisions
                        ],
                    }
                )
        except Exception as exc:
            log.exception(
                "phase_review.session_failed",
                session_id=session.session_id,
                error=str(exc),
            )
            session.fail(str(exc))
        finally:
            if session.status == "done":
                publish_sse(
                    "plan.phase_review_completed",
                    {"session_id": session.session_id},
                )
            else:
                publish_sse(
                    "plan.phase_review_failed",
                    {"session_id": session.session_id, "error": session.error},
                )

    threading.Thread(
        target=run, daemon=True, name=f"phase-review-{session.session_id}"
    ).start()

    return SessionAccepted(session_id=session.session_id, status=session.status)


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
    _publish_goal_dispatch_failures(result.goals_failed)
    return ApproveArchitectureResponse(
        decisions_applied=result.decisions_applied,
        goals_dispatched=result.goals_dispatched,
        plan_status=result.plan_status,
        goals_failed=[
            GoalDispatchFailureResponse(goal_name=f.goal_name, error=f.error)
            for f in result.goals_failed
        ],
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
    for goal_id in result.goals_dispatched:
        publish_sse("goal.dispatched", {"goal_id": goal_id})
    _publish_goal_dispatch_failures(result.goals_failed)
    return ApprovePhaseResponse(
        decisions_applied=result.decisions_applied,
        goals_dispatched=result.goals_dispatched,
        plan_status=result.plan_status,
        goals_failed=[
            GoalDispatchFailureResponse(goal_name=f.goal_name, error=f.error)
            for f in result.goals_failed
        ],
    )


# ── Resume Phase Dispatch ─────────────────────────────────────────────────────

@router.post(
    "/resume-dispatch",
    response_model=ResumeDispatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Resume Phase Goal Dispatch",
    description=(
        "**Recovery action.** Re-dispatches goals for the active phase that "
        "never got created — e.g. when a goal's branch creation failed during "
        "approve-architecture, leaving the phase under-populated with no "
        "automatic retry. Reloads each missing goal's spec from the completed "
        "planner session and dispatches it. Idempotent: already-dispatched "
        "goals are skipped."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "model": PlanConflictResponse,
            "description": (
                "Plan is not in `phase_active` status. The body reports the "
                "current vs expected status."
            ),
        }
    },
)
def resume_dispatch(orchestrator: PlanOrchestratorDep) -> ResumeDispatchResponse:
    result = orchestrator.resume_phase_dispatch()
    for goal_id in result.goals_dispatched:
        publish_sse("goal.dispatched", {"goal_id": goal_id})
    _publish_goal_dispatch_failures(result.goals_failed)
    return ResumeDispatchResponse(
        goals_dispatched=result.goals_dispatched,
        plan_status=result.plan_status,
        goals_failed=[
            GoalDispatchFailureResponse(goal_name=f.goal_name, error=f.error)
            for f in result.goals_failed
        ],
    )
