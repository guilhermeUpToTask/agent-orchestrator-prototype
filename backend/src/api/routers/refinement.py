"""
src/api/routers/refinement.py — Plan refinement endpoints.

Refinement is a multi-minute LLM session, so it follows the session
pattern instead of running inside the request:

  POST /plan/refine               202 + session_id; runs on the executor
  GET  /plan/sessions/{id}        state + terminal result of any session

Per-action progress streams over SSE as plan.refinement_action (published
from the executor as the planner acts), and completion as
plan.refine_completed / plan.refine_failed.
"""
from __future__ import annotations

import threading

import structlog
from fastapi import APIRouter, HTTPException, status

from src.api.dependencies import RunRefinementUseCaseDep
from src.api.schemas.common import ErrorResponse
from src.api.schemas.refinement import RefineRequest
from src.api.schemas.sessions import SessionAccepted, SessionStatusResponse
from src.api.sessions import registry
from src.api.sse import publish_sse

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/plan", tags=["plan"])


@router.post(
    "/refine",
    response_model=SessionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Refine Plan",
    description=(
        "Starts a tactical LLM refinement session for the given message and "
        "returns immediately with a session id. The planner may add, update, "
        "or remove tasks and goals. Actions stream over SSE as "
        "`plan.refinement_action`; the outcome (actions taken, success, error) "
        "is available from `GET /plan/sessions/{session_id}` once the status "
        "is `done` or `failed`. Optionally scoped via `focused_node_id` / "
        "`focused_goal_id`."
    ),
)
async def refine_plan(
    payload: RefineRequest,
    use_case: RunRefinementUseCaseDep,
) -> SessionAccepted:
    session = registry.create("refine")

    def run() -> None:
        try:
            result = use_case.execute(
                user_message=payload.message,
                focused_node_id=payload.focused_node_id,
                focused_goal_id=payload.focused_goal_id,
            )
            for action in result.actions_taken:
                publish_sse("plan.refinement_action", {"action": action})
            outcome = {
                "planner_session_id": result.session_id,
                "actions_taken": result.actions_taken,
                "succeeded": result.succeeded,
                "error": result.error,
            }
            if result.succeeded:
                session.complete(outcome)
            else:
                session.result = outcome
                session.fail(result.error or "refinement failed")
        except Exception as exc:
            log.exception(
                "refine.session_failed", session_id=session.session_id, error=str(exc)
            )
            session.fail(str(exc))
        finally:
            event = (
                "plan.refine_completed"
                if session.status == "done"
                else "plan.refine_failed"
            )
            publish_sse(event, {"session_id": session.session_id})

    # Daemon thread, not the loop's executor — see discovery.py.
    threading.Thread(
        target=run, daemon=True, name=f"refine-{session.session_id}"
    ).start()

    return SessionAccepted(session_id=session.session_id, status=session.status)


@router.get(
    "/sessions/{session_id}",
    response_model=SessionStatusResponse,
    summary="Get Session",
    description=(
        "Read the state of any long-running plan session (refinement or "
        "discovery). Terminal sessions carry their outcome in `result`."
    ),
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
)
async def get_session(session_id: str) -> SessionStatusResponse:
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No session '{session_id}'.",
        )
    return SessionStatusResponse(
        session_id=session.session_id,
        kind=session.kind,
        status=session.status,
        question=session.current_question,
        result=session.result,
        error=session.error,
    )
