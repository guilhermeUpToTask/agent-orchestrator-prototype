"""
src/api/sessions.py — registry for long-running API sessions.

Multi-minute planner work (discovery Q&A, refinement) no longer runs inside
a single HTTP request. POST endpoints return 202 with a session id, the
work runs on the executor, progress streams over SSE, and GET endpoints
read the state captured here.

Completion is an explicit state transition performed by the session runner
(in a finally block), never inferred from timeouts — the old global-queue
design desynchronized whenever an LLM turn outlasted the poll timeout.
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

log = structlog.get_logger(__name__)

# How long an interactive session waits for the user's answer before the
# planner thread gives up. Frees abandoned threadpool threads.
ANSWER_TIMEOUT_SECONDS = 1800.0

# Terminal sessions are kept around for late GET polls, then dropped.
_SESSION_TTL_SECONDS = 3600.0

STATUS_RUNNING = "running"
STATUS_WAITING_INPUT = "waiting_input"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

_TERMINAL = {STATUS_DONE, STATUS_FAILED}


class SessionAbandoned(Exception):
    """Raised inside the planner thread when no answer arrives in time."""


@dataclass
class ApiSession:
    session_id: str
    kind: str  # "discovery" | "refine" | "architecture"
    status: str = STATUS_RUNNING
    current_question: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.monotonic)
    answer_q: "queue.Queue[str]" = field(default_factory=lambda: queue.Queue(maxsize=1))
    cancel_requested: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    def request_cancel(self) -> None:
        """Signal a cooperative stop; the planner loop polls this between turns."""
        self.cancel_requested = True

    # ------------------------------------------------------------------
    # Interactive Q&A plumbing (called from the planner executor thread)
    # ------------------------------------------------------------------

    def ask(self, question: str) -> str:
        """Expose *question*, then block until the user answers (or timeout)."""
        self.current_question = question
        self.status = STATUS_WAITING_INPUT
        try:
            answer = self.answer_q.get(timeout=ANSWER_TIMEOUT_SECONDS)
        except queue.Empty:
            raise SessionAbandoned(
                f"No answer within {ANSWER_TIMEOUT_SECONDS:.0f}s — session abandoned"
            ) from None
        self.current_question = None
        self.status = STATUS_RUNNING
        return answer

    def complete(self, result: Optional[dict[str, Any]] = None) -> None:
        self.result = result
        self.current_question = None
        self.status = STATUS_DONE

    def fail(self, error: str) -> None:
        self.error = error
        self.current_question = None
        self.status = STATUS_FAILED


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, ApiSession] = {}
        self._lock = threading.Lock()

    def create(self, kind: str) -> ApiSession:
        session = ApiSession(session_id=str(uuid.uuid4())[:8], kind=kind)
        with self._lock:
            self._gc_locked()
            self._sessions[session.session_id] = session
        log.info("api.session_created", session_id=session.session_id, kind=kind)
        return session

    def get(self, session_id: str) -> Optional[ApiSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def active(self, kind: str) -> Optional[ApiSession]:
        """Return the running/waiting session of *kind*, if any."""
        with self._lock:
            for s in self._sessions.values():
                if s.kind == kind and not s.is_terminal:
                    return s
        return None

    def latest(self, kind: str) -> Optional[ApiSession]:
        """Return the most recently created session of *kind* (any status).

        Powers reload-resilient status reads: after a run completes the session
        is still here until TTL, so the approval gate can be rebuilt without
        relying on ephemeral client state.
        """
        with self._lock:
            candidates = [s for s in self._sessions.values() if s.kind == kind]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.created_at)

    def _gc_locked(self) -> None:
        now = time.monotonic()
        stale = [
            sid
            for sid, s in self._sessions.items()
            if s.is_terminal and now - s.created_at > _SESSION_TTL_SECONDS
        ]
        for sid in stale:
            del self._sessions[sid]


# Module-level singleton shared by the plan routers.
registry = SessionRegistry()
