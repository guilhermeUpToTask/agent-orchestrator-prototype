"""The reasoner port: the planning LLM behind the phase machine.

Adapters (StubReasoner, the OpenAI-compatible reasoner) implement it; the
conversation use cases and the PlanningHandler own the transactions and the
phase transitions — the reasoner reads, never persists.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from src.domain.aggregates.planner_orchestrator import Plan
from src.domain.entities.goal import Goal

ChatRole = Literal["user", "assistant"]


class ChatMessage(BaseModel):
    """One turn of a plan's DISCOVERY/REPLANNING conversation. Persisted by the
    ChatStore (app port) outside the plan transaction — display history, never
    plan state."""

    role: ChatRole
    content: str
    created_at: datetime
    meta: dict[str, str | bool | int] = Field(default_factory=dict)


@runtime_checkable
class Reasoner(Protocol):
    """The planning LLM (one-shot transforms per planning phase). Each method is
    a pure content transform — it reads, never persists; the PlanningHandler /
    conversation use cases own the transaction and the phase transition.

    DISCOVERY and REPLANNING are conversational (each user message is one call);
    ARCHITECTURE and ENRICHING are autonomous worker steps."""

    async def draft_goals(self, brief: str) -> list[Goal]: ...
    async def structure_goals(self, plan: Plan) -> list[Goal]: ...
    async def enrich_goals(self, plan: Plan) -> list[Goal]: ...
    async def replan_goals(self, plan: Plan, message: str) -> list[Goal]: ...
