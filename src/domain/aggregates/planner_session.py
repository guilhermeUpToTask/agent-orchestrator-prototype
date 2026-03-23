"""
src/domain/aggregates/planner_session.py — PlannerSession aggregate.

Lifecycle:
  PENDING → RUNNING → COMPLETED
                    → FAILED

Every conversation turn (LLM message, tool call, tool result) is persisted
as part of the session — turn-by-turn audit trail, not just final output.

Direct field mutation from outside the aggregate is forbidden — all state
changes go through methods that call _bump().
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from src.domain.value_objects.task import HistoryEntry


class PlannerSessionStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"


class PlannerMode(str, Enum):
    DISCOVERY     = "discovery"
    ARCHITECTURE  = "architecture"
    PHASE_REVIEW  = "phase_review"
    TACTICAL      = "tactical"     # for future issue/bug use


class SessionTurn(BaseModel):
    """One turn of the multi-turn planning conversation."""
    role: str                   # "assistant" | "tool_result"
    content: list[dict]         # raw Anthropic message content blocks
    turn_index: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"frozen": True}


class PlannerSession(BaseModel):
    """
    Authoritative aggregate for a single planning session.

    All mutations go through named methods that call _bump() — never assign
    to fields directly from outside the aggregate.
    """

    session_id: str
    user_input: str
    status: PlannerSessionStatus = PlannerSessionStatus.PENDING
    mode: PlannerMode = PlannerMode.DISCOVERY
    reasoning: str = ""
    raw_llm_output: str = ""
    roadmap_data: Optional[dict] = None
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    goals_dispatched: list[str] = Field(default_factory=list)  # goal_ids
    turns: list[SessionTurn] = Field(default_factory=list)
    failure_reason: Optional[str] = None
    state_version: int = 1
    history: list[HistoryEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(cls, user_input: str, mode: PlannerMode = PlannerMode.DISCOVERY) -> "PlannerSession":
        return cls(
            session_id=f"plan-{uuid4().hex[:12]}",
            user_input=user_input,
            mode=mode,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bump(self, event: str, actor: str, detail: dict[str, Any] | None = None) -> None:
        self.state_version += 1
        self.updated_at = datetime.now(timezone.utc)
        self.history.append(
            HistoryEntry(event=event, actor=actor, detail=detail or {})
        )

    def _assert_status(self, *expected: PlannerSessionStatus) -> None:
        if self.status not in expected:
            raise ValueError(
                f"PlannerSession '{self.session_id}' is '{self.status.value}'; "
                f"expected one of {[s.value for s in expected]}."
            )

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def start(self) -> "PlannerSession":
        """PENDING → RUNNING."""
        self._assert_status(PlannerSessionStatus.PENDING)
        self.status = PlannerSessionStatus.RUNNING
        self._bump("planner.session_started", "planner")
        return self

    def add_turn(
        self,
        role: str,
        content: list[dict],
        turn_index: int,
    ) -> "PlannerSession":
        """Append a conversation turn and persist it immediately."""
        self._assert_status(PlannerSessionStatus.RUNNING)
        turn = SessionTurn(role=role, content=content, turn_index=turn_index)
        self.turns.append(turn)
        self._bump(
            "planner.turn_added",
            "planner",
            {"role": role, "turn_index": turn_index},
        )
        return self

    def record_roadmap_candidate(self, roadmap_data: dict) -> "PlannerSession":
        """Record the roadmap data mid-session when submit_final_roadmap is called."""
        self._assert_status(PlannerSessionStatus.RUNNING)
        self.roadmap_data = roadmap_data
        self._bump("planner.roadmap_candidate_recorded", "planner")
        return self

    def complete(
        self,
        reasoning: str,
        raw_llm_output: str,
        validation_errors: list[str],
        validation_warnings: list[str],
    ) -> "PlannerSession":
        """RUNNING → COMPLETED. roadmap_data must already be set."""
        self._assert_status(PlannerSessionStatus.RUNNING)
        if self.roadmap_data is None:
            raise ValueError(
                f"PlannerSession '{self.session_id}' cannot complete without roadmap_data. "
                "Call record_roadmap_candidate() first."
            )
        self.status = PlannerSessionStatus.COMPLETED
        self.reasoning = reasoning
        self.raw_llm_output = raw_llm_output
        self.validation_errors = list(validation_errors)
        self.validation_warnings = list(validation_warnings)
        self._bump(
            "planner.session_completed",
            "planner",
            {
                "validation_errors": len(validation_errors),
                "validation_warnings": len(validation_warnings),
            },
        )
        return self

    def fail(self, reason: str, raw_llm_output: str = "") -> "PlannerSession":
        """RUNNING → FAILED."""
        self._assert_status(PlannerSessionStatus.RUNNING)
        self.status = PlannerSessionStatus.FAILED
        self.failure_reason = reason
        self.raw_llm_output = raw_llm_output
        self._bump("planner.session_failed", "planner", {"reason": reason})
        return self

    def record_goal_dispatched(
        self, goal_id: str, goal_name: str
    ) -> "PlannerSession":
        """Record that a goal was successfully dispatched."""
        self.goals_dispatched.append(goal_id)
        self._bump(
            "planner.goal_dispatched",
            "planner",
            {"goal_id": goal_id, "goal_name": goal_name},
        )
        return self

    def record_dispatch_failure(self, goal_name: str, reason: str) -> "PlannerSession":
        """Record that a goal could not be dispatched."""
        self.validation_errors.append(f"Dispatch failed for '{goal_name}': {reason}")
        self._bump(
            "planner.dispatch_failed",
            "planner",
            {"goal_name": goal_name, "reason": reason},
        )
        return self

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_terminal(self) -> bool:
        return self.status in (
            PlannerSessionStatus.COMPLETED,
            PlannerSessionStatus.FAILED,
        )

    def has_valid_roadmap(self) -> bool:
        """True only if COMPLETED with no validation errors and roadmap_data set."""
        return (
            self.status == PlannerSessionStatus.COMPLETED
            and not self.validation_errors
            and self.roadmap_data is not None
        )
