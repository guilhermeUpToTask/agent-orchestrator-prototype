"""Application-layer ports + re-exports of the domain ports.

The five execution/side-channel contracts (Clock, AgentEventSink,
WorkspaceHandle/Workspace, AgentRunner, Reasoner) are DOMAIN ports —
they live in src/domain/ports/ and are re-exported here so use cases,
adapters, and tests keep one import path. What remains defined here is
app-specific: the TaskFailed signal and the transaction machinery
(Outbox, UnitOfWork).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.events.base import DomainEvent
from src.domain.ports import (
    AgentEventSink,
    AgentRunner,
    Clock,
    Reasoner,
    Workspace,
    WorkspaceHandle,
)
from src.domain.ports.reasoner_port import ChatMessage
from src.domain.repositories.planner_repo import PlanRepository
from src.domain.value_objects.lifecycle import FailureKind

__all__ = [
    "AgentEventSink",
    "AgentRunner",
    "ChatMessage",
    "ChatStore",
    "Clock",
    "Outbox",
    "Reasoner",
    "TaskFailed",
    "UnitOfWork",
    "Workspace",
    "WorkspaceHandle",
]


class TaskFailed(Exception):
    """Raised by an AgentRunner when a task run fails. Carries a human-readable
    `reason` plus a typed `kind` (the shared failure taxonomy) that the domain
    RetryPolicy classifies (retryable vs terminal)."""

    def __init__(self, reason: str, kind: FailureKind | None = None) -> None:
        self.reason = reason
        self.kind = kind
        super().__init__(reason)


@runtime_checkable
class ChatStore(Protocol):
    """Per-plan conversation history (DISCOVERY / REPLANNING). Writes run
    OUTSIDE the plan UnitOfWork on their own short transactions: a lost display
    reply never loses plan state, and a state rollback never erases what the
    user actually said."""

    def append(self, plan_id: str, message: ChatMessage) -> None: ...
    def list(self, plan_id: str) -> list[ChatMessage]: ...


@runtime_checkable
class Outbox(Protocol):
    """Coarse domain events, added INSIDE the state transaction (transactional
    outbox). The UnitOfWork commits state + outbox atomically."""

    def add(self, event: DomainEvent) -> None: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """Transaction boundary. Owns a PlanRepository and an Outbox; entering starts
    a transaction, exiting commits (or rolls back on exception). This is how
    state + outbox become atomic.

    plans/outbox are read-only properties on the protocol so concrete
    implementations' narrower attribute types remain assignable (covariance)."""

    @property
    def plans(self) -> PlanRepository: ...
    @property
    def outbox(self) -> Outbox: ...

    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(self, *exc: object) -> None: ...
