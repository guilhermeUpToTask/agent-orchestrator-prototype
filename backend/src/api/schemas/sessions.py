"""src/api/schemas/sessions.py — long-running API session DTOs.

Long planner operations (discovery, refinement) return 202 + a session id;
clients follow progress over SSE and read state/results via GET.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel

SessionStatus = Literal["running", "waiting_input", "done", "failed"]


class SessionAccepted(BaseModel):
    """Returned with 202 when a long-running session is started."""
    session_id: str
    status: SessionStatus


class SessionStatusResponse(BaseModel):
    """Current state of a long-running session."""
    session_id: str
    kind: Literal["discovery", "refine"]
    status: SessionStatus
    question: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
