"""The agent-execution port: run ONE task, return a result.

Failures are signaled with the application-layer `TaskFailed` exception
(src/app/ports.py) carrying a `FailureKind` from the shared taxonomy; the
domain RetryPolicy classifies it retryable vs terminal.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.task import Task
from src.domain.ports.telemetry_port import AgentEventSink
from src.domain.ports.workplace_port import WorkspaceHandle
from src.domain.value_objects.tasks_vos import TaskResult


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
