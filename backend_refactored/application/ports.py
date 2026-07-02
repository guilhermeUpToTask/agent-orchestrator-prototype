"""Application-layer ports. These are the interfaces the use cases depend on;
adapters (infra) implement them. Distinct from domain/repositories ports, which
are about persisting domain objects — these are about execution + side channels.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from domain.entities.agent_spec import AgentSpec
from domain.entities.task import Task
from domain.events.agent_events import AgentEvent
from domain.events.base import DomainEvent
from domain.policies.retry_policies import RetryPolicy
from domain.repositories.planner_repo import PlanRepository
from domain.value_objects.tasks_vos import TaskResult


@runtime_checkable
class Clock(Protocol):
    """Injected time source. Keeps the domain scan pure (now is passed in) and
    makes time deterministically controllable in tests."""

    def now(self) -> "datetime": ...


class TaskFailed(Exception):
    """Raised by an AgentRunner when a task run fails. Carries a `reason` the
    domain RetryPolicy classifies (retryable vs terminal)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@runtime_checkable
class AgentEventSink(Protocol):
    """Best-effort telemetry sink for fine-grained agent runtime events."""

    async def emit(self, event: AgentEvent) -> None: ...


@runtime_checkable
class WorkspaceHandle(Protocol):
    @property
    def path(self) -> str: ...


@runtime_checkable
class Workspace(Protocol):
    """Git-branching seam. NoOp now (handle.path = shared dir); git adapter later
    makes begin/commit/discard real branch operations."""

    async def begin(
        self, plan_id: str, task_id: str, attempt: int
    ) -> WorkspaceHandle: ...
    async def commit(self, handle: WorkspaceHandle) -> None: ...
    async def discard(self, handle: WorkspaceHandle) -> None: ...


@runtime_checkable
class AgentRunner(Protocol):
    """Executes ONE task and returns a result (or raises TaskFailed). Knows
    NOTHING about retries, backoff, ordering, or other tasks — it is a pure
    "execute this task now" hand that the use case directs. Retry/backoff is owned
    by the orchestration layer (the use case sets a durable retry gate; the worker
    re-picks when the gate expires). The runner is never handed the RetryPolicy."""

    async def run(
        self,
        task: Task,
        spec: AgentSpec,
        *,
        idempotency_key: str,
        event_sink: AgentEventSink,
        workspace: WorkspaceHandle,
    ) -> TaskResult: ...


@runtime_checkable
class Reasoner(Protocol):
    """The planning LLM (one-shot transforms). Stubbed minimally here; the LLM
    phases call these. Built out when the planning phases are implemented."""

    async def draft_plan(self, brief: str, policy: RetryPolicy) -> dict: ...


@runtime_checkable
class Outbox(Protocol):
    """Coarse domain events, added INSIDE the state transaction (transactional
    outbox). The UnitOfWork commits state + outbox atomically."""

    def add(self, event: DomainEvent) -> None: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """Transaction boundary. Owns a PlanRepository and an Outbox; entering starts
    a transaction, exiting commits (or rolls back on exception). This is how
    state + outbox become atomic."""

    plans: PlanRepository
    outbox: Outbox

    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(self, *exc: object) -> None: ...
