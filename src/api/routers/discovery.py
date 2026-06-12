"""
src/api/routers/discovery.py — Interactive discovery session endpoints.

The discovery flow is a long-running interactive Q&A between the user
and the LLM planner.  It uses two asyncio queues to shuttle questions
and answers between the HTTP request cycle and the blocking executor thread.

Covers:
  POST /plan/discovery/start    kick off a discovery session, returns first question
  POST /plan/discovery/message  send an answer, returns the next question or completion
"""
from __future__ import annotations

import asyncio
import queue

from fastapi import APIRouter, status

from src.api.dependencies import PlanOrchestratorDep
from src.api.schemas.common import ErrorResponse
from src.api.schemas.discovery import (
    DiscoveryMessageRequest,
    DiscoveryMessageResponse,
    DiscoveryStartResponse,
)

router = APIRouter(prefix="/plan/discovery", tags=["plan"])

# Module-level queues: one session at a time (single-user orchestrator model).
# A future multi-user variant would key these per session_id.
_question_q: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
_answer_q: queue.Queue[str] = queue.Queue(maxsize=1)

# Track active discovery session state
_discovery_active: bool = False

_FIRST_QUESTION_TIMEOUT = 30.0
_SUBSEQUENT_TIMEOUT = 60.0


@router.post(
    "/start",
    response_model=DiscoveryStartResponse,
    status_code=status.HTTP_200_OK,
    summary="Start Discovery Session",
    description=(
        "Launches the interactive discovery session in a background thread. "
        "Returns either the first question from the planner, or `done=true` with "
        "the completed brief if the planner needs no further input."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "A discovery session is already in progress.",
        }
    },
)
async def start_discovery(orchestrator: PlanOrchestratorDep) -> DiscoveryStartResponse:
    global _discovery_active
    
    if _discovery_active:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A discovery session is already in progress.",
        )
    
    _discovery_active = True
    loop = asyncio.get_running_loop()

    def io_handler(question: str) -> str:
        loop.call_soon_threadsafe(_question_q.put_nowait, question)
        return _answer_q.get()

    future = loop.run_in_executor(
        None,
        lambda: orchestrator.start_discovery(io_handler=io_handler),
    )

    try:
        question = await asyncio.wait_for(
            _question_q.get(), timeout=_FIRST_QUESTION_TIMEOUT
        )
        return DiscoveryStartResponse(question=question, done=False)
    except asyncio.TimeoutError:
        try:
            result = await future
        finally:
            # Reset even when the planner session raised, or every subsequent
            # /start would 409 until the server restarts.
            _discovery_active = False
        return DiscoveryStartResponse(
            done=True,
            brief=result.brief.model_dump() if result.brief else None,
        )


@router.post(
    "/message",
    response_model=DiscoveryMessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Send Discovery Message",
    description=(
        "Send an answer to the current discovery question. "
        "Returns the next question from the planner, or `done=true` with the "
        "final brief when all questions are answered."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "No active discovery session.",
        }
    },
)
async def send_discovery_message(
    payload: DiscoveryMessageRequest,
) -> DiscoveryMessageResponse:
    global _discovery_active
    
    if not _discovery_active:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No active discovery session. Call /plan/discovery/start first.",
        )
    
    _answer_q.put(payload.message)
    try:
        question = await asyncio.wait_for(
            _question_q.get(), timeout=_SUBSEQUENT_TIMEOUT
        )
        return DiscoveryMessageResponse(question=question, done=False)
    except asyncio.TimeoutError:
        _discovery_active = False
        return DiscoveryMessageResponse(question=None, done=True)
