"""Application-layer ports + re-exports of the domain ports.

The five execution/side-channel contracts (Clock, AgentEventSink,
WorkspaceHandle/Workspace, AgentRunner, Reasoner) are DOMAIN ports —
they live in src/domain/ports/ and are re-exported here so use cases,
adapters, and tests keep one import path. What remains defined here is
app-specific: the TaskFailed signal and the transaction machinery
(Outbox, UnitOfWork).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from src.app.execution_records import ExecutionRecordRepository
from src.app.runtime_failures import RuntimeFailure
from src.domain.events.base import DomainEvent
from src.domain.ports import (
    AgentEventSink,
    AgentRunner,
    Clock,
    Reasoner,
    Workspace,
    WorkspaceHandle,
)
from src.domain.ports.reasoner_port import (
    ChatMessage,
    ConversationMode,
    ReasonerReply,
)
from src.domain.repositories.planner_repo import PlanRepository
from src.domain.value_objects.lifecycle import FailureKind

__all__ = [
    "AgentEventSink",
    "AgentRunner",
    "ChatMessage",
    "ChatStore",
    "Clock",
    "ConversationMode",
    "ExecutionRecordRepository",
    "Outbox",
    "Reasoner",
    "ReasonerReply",
    "ReasonerUnavailable",
    "TaskFailed",
    "UnitOfWork",
    "Workspace",
    "WorkspaceHandle",
    "VerificationExecutor",
    "CommandExecution",
]


@dataclass(frozen=True)
class CommandExecution:
    command: str
    exit_code: int
    started_at: datetime
    finished_at: datetime
    bounded_output_ref: str


@runtime_checkable
class VerificationExecutor(Protocol):
    async def changed_paths(
        self,
        workspace_path: str,
        base_ref: str | None = None,
    ) -> list[str]: ...
    async def run(
        self,
        workspace_path: str,
        commands: list[str],
    ) -> list[CommandExecution]: ...


class TaskFailed(Exception):
    """Raised by an AgentRunner when a task run fails. Carries a human-readable
    `reason` plus a typed `kind` (the shared failure taxonomy) that the domain
    RetryPolicy classifies (retryable vs terminal)."""

    def __init__(
        self,
        reason: str,
        kind: FailureKind | None = None,
        *,
        failure: RuntimeFailure | None = None,
    ) -> None:
        resolved_kind = failure.kind if failure is not None else kind
        if failure is None:
            failure = RuntimeFailure(
                kind=resolved_kind or FailureKind.TOOL_ERROR,
                safe_message=reason[:500],
                retryable=(resolved_kind not in {FailureKind.AUTH_ERROR, FailureKind.TOKEN_LIMIT}),
            )
        self.failure = failure
        self.reason = failure.safe_message
        self.kind = failure.kind
        super().__init__(self.reason)


class ReasonerUnavailable(Exception):
    """Raised by a Reasoner when it cannot produce a usable turn/artifact — the
    planning-phase analog of TaskFailed. `transient` marks a failure worth
    retrying (rate limit, timeout, upstream blip) versus a permanent config
    error (model lacks tool use, bad key). The PlanningHandler catches this to
    arm the plan-level backoff gate or fail the plan.

    Kept app-side (like TaskFailed) so the handler stays free of infra imports;
    the infra ReasonerError subclasses it, so the concrete adapter's raise
    crosses the boundary as this type without app ever importing infra."""

    def __init__(self, reason: str, *, transient: bool = False) -> None:
        self.reason = reason
        self.transient = transient
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
    """Transaction boundary. Owns Plan, execution-ledger, and Outbox repositories;
    entering starts a transaction and exiting commits (or rolls back on exception).
    This is how state + execution identity + outbox become atomic.

    plans/outbox are read-only properties on the protocol so concrete
    implementations' narrower attribute types remain assignable (covariance)."""

    @property
    def plans(self) -> PlanRepository: ...
    @property
    def outbox(self) -> Outbox: ...
    @property
    def executions(self) -> ExecutionRecordRepository: ...

    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(self, *exc: object) -> None: ...
