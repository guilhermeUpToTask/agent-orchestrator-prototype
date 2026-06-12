"""
src/api/routers/discovery.py — Interactive discovery session endpoints.

Discovery is a long-running interactive Q&A between the user and the LLM
planner. It follows the session pattern:

  POST /plan/discovery/start                202 + session_id; planner runs
                                            on the executor
  POST /plan/discovery/{session_id}/message 202; answer the current question
  GET  /plan/discovery/{session_id}         current status / question / brief

Each question is also published over SSE as plan.discovery_question, and
completion as plan.discovery_completed / plan.discovery_failed.

Completion is an explicit state transition made by the runner (finally
block) — never inferred from response timeouts, which desynchronized the
old design whenever an LLM turn ran long. Session state lives in
src/api/sessions.py keyed by session id; a failed session never blocks the
next start.
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException, status

from src.api.dependencies import PlanOrchestratorDep
from src.api.schemas.common import ErrorResponse
from src.api.schemas.discovery import DiscoveryMessageRequest
from src.api.schemas.sessions import SessionAccepted, SessionStatusResponse
from src.api.sessions import (
    STATUS_WAITING_INPUT,
    ApiSession,
    registry,
)
from src.api.sse import publish_sse

import structlog

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/plan/discovery", tags=["plan"])


def _session_response(session: ApiSession) -> SessionStatusResponse:
    return SessionStatusResponse(
        session_id=session.session_id,
        kind="discovery",
        status=session.status,  # type: ignore[arg-type]
        question=session.current_question,
        result=session.result,
        error=session.error,
    )


@router.post(
    "/start",
    response_model=SessionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start Discovery Session",
    description=(
        "Launches the interactive discovery session in a background thread and "
        "returns immediately with a session id. Questions are published over "
        "SSE as `plan.discovery_question` and are also readable via "
        "`GET /plan/discovery/{session_id}`."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "A discovery session is already in progress.",
        }
    },
)
async def start_discovery(orchestrator: PlanOrchestratorDep) -> SessionAccepted:
    if registry.active("discovery") is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A discovery session is already in progress.",
        )

    session = registry.create("discovery")

    def io_handler(question: str) -> str:
        publish_sse(
            "plan.discovery_question",
            {"session_id": session.session_id, "question": question},
        )
        return session.ask(question)

    def run() -> None:
        try:
            result = orchestrator.start_discovery(io_handler=io_handler)
            if result.failure_reason:
                session.fail(result.failure_reason)
            else:
                session.complete(
                    {"brief": result.brief.model_dump() if result.brief else None}
                )
        except Exception as exc:
            log.exception(
                "discovery.session_failed",
                session_id=session.session_id,
                error=str(exc),
            )
            session.fail(str(exc))
        finally:
            event = (
                "plan.discovery_completed"
                if session.status == "done"
                else "plan.discovery_failed"
            )
            publish_sse(event, {"session_id": session.session_id})

    # Daemon thread, not the loop's executor: an abandoned session parked on
    # answer_q.get() must never block server shutdown.
    threading.Thread(
        target=run, daemon=True, name=f"discovery-{session.session_id}"
    ).start()

    return SessionAccepted(session_id=session.session_id, status=session.status)  # type: ignore[arg-type]


@router.post(
    "/{session_id}/message",
    response_model=SessionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Answer Discovery Question",
    description=(
        "Submit the user's answer to the session's current question. The next "
        "question (or completion) arrives via SSE / the session GET endpoint."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "The session is not waiting for input.",
        },
    },
)
async def send_discovery_message(
    session_id: str, payload: DiscoveryMessageRequest
) -> SessionAccepted:
    session = registry.get(session_id)
    if session is None or session.kind != "discovery":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No discovery session '{session_id}'.",
        )
    if session.status != STATUS_WAITING_INPUT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is '{session.status}', not waiting for input.",
        )

    session.answer_q.put(payload.message)
    return SessionAccepted(session_id=session.session_id, status="running")


@router.get(
    "/{session_id}",
    response_model=SessionStatusResponse,
    summary="Get Discovery Session",
    description=(
        "Read the session's current state: `waiting_input` carries the pending "
        "question; `done` carries the final brief in `result.brief`."
    ),
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
)
async def get_discovery_session(session_id: str) -> SessionStatusResponse:
    session = registry.get(session_id)
    if session is None or session.kind != "discovery":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No discovery session '{session_id}'.",
        )
    return _session_response(session)
