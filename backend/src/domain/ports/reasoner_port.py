"""The reasoner port: the planning LLM behind the phase machine.

Two methods, matching the two places an LLM actually plans:

  converse    — DISCOVERY / REPLANNING chat. Multi-turn: each user message is
                one call; a reply without goals keeps the conversation open, a
                reply WITH goals is the roadmap commit that moves the plan into
                ARCHITECTURE (the caller owns the transaction + transition).
  enrich_goal — the ENRICHING JIT step: break ONE goal into a small ordered set
                of plain executable tasks (capability ids from the catalog).

ARCHITECTURE deliberately has no method: discovery commits the user-agreed
roadmap itself, so an autonomous re-structuring pass is redundant for the
prototype — the phase is a no-LLM passthrough in the PlanningHandler (that
handler is the seam if a real structuring pass returns).

Adapters (StubReasoner, the OpenAI-compatible reasoner) implement it; the
conversation use cases and the PlanningHandler own the transactions and the
phase transitions — the reasoner reads, never persists.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, Field

from src.domain.aggregates.planner_orchestrator import Plan
from src.domain.entities.capability import Capability
from src.domain.entities.goal import Goal
from src.domain.entities.task import Task

ChatRole = Literal["user", "assistant"]

ConversationMode = Literal["discovery", "replanning"]


class ChatMessage(BaseModel):
    """One turn of a plan's DISCOVERY/REPLANNING conversation. Persisted by the
    ChatStore (app port) outside the plan transaction — display history, never
    plan state."""

    role: ChatRole
    content: str
    created_at: datetime
    meta: dict[str, str | bool | int] = Field(default_factory=dict)


class ReasonerReply(BaseModel):
    """One converse() turn. goals=None means "still conversing" (the message is
    a question/reply to show the user); a goal list is the roadmap commit."""

    message: str
    goals: list[Goal] | None = None


@runtime_checkable
class Reasoner(Protocol):
    """The planning LLM. Pure content transforms — it reads, never persists;
    the callers own the transaction and the phase transition."""

    async def converse(
        self,
        plan: Plan,
        history: Sequence[ChatMessage],
        message: str,
        mode: ConversationMode,
    ) -> ReasonerReply: ...

    async def enrich_goal(
        self,
        plan: Plan,
        goal: Goal,
        capabilities: Sequence[Capability],
    ) -> list[Task]: ...
