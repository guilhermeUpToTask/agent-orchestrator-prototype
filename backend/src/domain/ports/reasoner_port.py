"""The reasoner port: the planning LLM behind the phase machine.

Four purpose-specific content transforms implement the cyclic flow:

  converse             — normalize a brief and propose one IntentCandidate.
  architect_cycle      — turn an approved intent into a stable-key GoalOutline DAG.
  enrich_goal_contract — freeze the head goal's contract and executable tasks JIT.
  enrich_goal          — quarantined compatibility transform for legacy plans only.

Adapters (StubReasoner, the OpenAI-compatible reasoner) implement it; the
conversation use cases and the PlanningHandler own the transactions and the
phase transitions — the reasoner reads, never persists.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, Field

from src.domain.aggregates.planner_orchestrator import Plan
from src.domain.entities.capability import Capability
from src.domain.entities.goal import Goal
from src.domain.entities.execution_contracts import GoalContract
from src.domain.entities.planning_artifacts import GoalOutline
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
    meta: dict[str, Any] = Field(default_factory=dict)


class IntentCandidate(BaseModel):
    """Reasoner-produced DTO; application code assigns identity and review state."""

    normalized_brief: str
    objective: str
    scope: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class ReasonerReply(BaseModel):
    """One conversation turn. Intent is the canonical commit artifact; goals is
    retained only for reading/quarantining pre-cyclic compatibility traffic."""

    message: str
    goals: list[Goal] | None = None
    intent: IntentCandidate | None = None
    model_request_count: int = 0
    tool_turn_count: int = 0


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

    async def architect_cycle(self, plan: Plan) -> list[GoalOutline]: ...

    async def enrich_goal_contract(
        self,
        plan: Plan,
        goal: Goal,
        capabilities: Sequence[Capability],
    ) -> GoalContract: ...
