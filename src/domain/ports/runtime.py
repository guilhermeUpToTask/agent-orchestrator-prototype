"""
src/domain/ports/runtime.py — Agent runtime port.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from src.domain.entities.agent import AgentProps
from src.domain.value_objects.execution import AgentExecutionResult, ExecutionContext


class SessionHandle(ABC):
    """
    Opaque handle to a running agent session returned by AgentRuntimePort.
    Callers pass it back to send payloads, wait for completion, and terminate.
    """

    @property
    @abstractmethod
    def session_id(self) -> str: ...


class AgentRuntimePort(ABC):
    """
    Contract for starting, communicating with, and terminating agent sessions.
    Infrastructure provides adapters per agent type (Gemini CLI, Claude Code, etc.).
    """

    @abstractmethod
    def start_session(
        self,
        agent_props: AgentProps,
        workspace_path: str,
        env_vars: dict[str, str],
    ) -> SessionHandle:
        """Spawn the agent process and return a handle to the session."""
        ...

    @abstractmethod
    def send_execution_payload(
        self,
        handle: SessionHandle,
        context: ExecutionContext,
    ) -> None:
        """Send the task description and constraints to the running agent."""
        ...

    @abstractmethod
    def wait_for_completion(
        self,
        handle: SessionHandle,
        timeout_seconds: int = 600,
    ) -> AgentExecutionResult:
        """Block until the agent finishes or the timeout elapses."""
        ...

    @abstractmethod
    def terminate_session(self, handle: SessionHandle) -> None:
        """Forcefully terminate the agent session."""
        ...

    @abstractmethod
    def stream_logs(self, handle: SessionHandle) -> Iterator[str]:
        """Yield agent output lines as they arrive."""
        ...
