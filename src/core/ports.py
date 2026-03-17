"""
src/core/ports.py — Abstract port interfaces (hexagonal boundary).
Domain and application layers only depend on these ABCs; infra provides adapters.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Optional

from src.core.models import (
    AgentExecutionResult,
    AgentProps,
    DomainEvent,
    ExecutionContext,
    TaskAggregate,
)


# ---------------------------------------------------------------------------
# Repository port
# ---------------------------------------------------------------------------


class TaskRepositoryPort(ABC):
    """Persistence port for TaskAggregate. File-backed in prototype."""

    @abstractmethod
    def load(self, task_id: str) -> TaskAggregate:
        """Load task aggregate by ID. Raises KeyError if not found."""
        ...

    @abstractmethod
    def update_if_version(
        self,
        task_id: str,
        new_state: TaskAggregate,
        expected_version: int,
    ) -> bool:
        """
        Atomic compare-and-swap write.
        Returns True on success, False on version conflict.
        Must fsync before returning.
        """
        ...

    @abstractmethod
    def save(self, task: TaskAggregate) -> None:
        """Unconditional save (used for initial creation)."""
        ...

    @abstractmethod
    def append_history(self, task_id: str, event: str, actor: str, detail: dict) -> None:
        """Append a history entry without bumping state_version."""
        ...

    @abstractmethod
    def list_all(self) -> list[TaskAggregate]:
        """Return all tasks (used by reconciler)."""
        ...

    def get(self, task_id: str) -> "TaskAggregate | None":
        """Return task or None if not found. Default impl wraps load()."""
        try:
            return self.load(task_id)
        except KeyError:
            return None

    def delete(self, task_id: str) -> bool:
        """
        Remove the task record.  Returns True if deleted, False if not found.
        Default impl is a no-op that subclasses can override.
        """
        return False


# ---------------------------------------------------------------------------
# Agent registry port
# ---------------------------------------------------------------------------


class AgentRegistryPort(ABC):
    @abstractmethod
    def register(self, agent: AgentProps) -> None: ...

    @abstractmethod
    def deregister(self, agent_id: str) -> None: ...

    @abstractmethod
    def list_agents(self) -> list[AgentProps]: ...

    @abstractmethod
    def heartbeat(self, agent_id: str) -> None: ...

    @abstractmethod
    def get(self, agent_id: str) -> Optional[AgentProps]: ...


# ---------------------------------------------------------------------------
# Event port
# ---------------------------------------------------------------------------


class EventPort(ABC):
    @abstractmethod
    def publish(self, event: DomainEvent) -> None:
        """Publish event. Payload must be minimal (IDs only)."""
        ...

    @abstractmethod
    def subscribe(self, event_type: str, group: str, consumer: str) -> Iterator[DomainEvent]:
        """
        Block-subscribe to events of a given type using consumer groups.
        Each message is delivered to exactly one consumer in the group.
        """
        ...

    @abstractmethod
    def subscribe_many(
        self, event_types: list[str], group: str, consumer: str
    ) -> Iterator[DomainEvent]:
        """
        Block-subscribe to multiple event types in a single call.
        Yields events from any of the given types as they arrive.
        Use this instead of chaining multiple subscribe() calls — chaining
        blocking generators means only the first type is ever consumed.
        """
        ...


# ---------------------------------------------------------------------------
# Lease port
# ---------------------------------------------------------------------------


class LeasePort(ABC):
    @abstractmethod
    def create_lease(self, task_id: str, agent_id: str, lease_seconds: int) -> str:
        """Returns lease_token (opaque string)."""
        ...

    @abstractmethod
    def refresh_lease(self, lease_token: str, additional_seconds: int = 60) -> bool: ...

    @abstractmethod
    def revoke_lease(self, lease_token: str) -> bool: ...

    @abstractmethod
    def is_lease_active(self, task_id: str) -> bool: ...

    @abstractmethod
    def get_lease_agent(self, task_id: str) -> Optional[str]:
        """Return agent_id holding active lease, or None."""
        ...


# ---------------------------------------------------------------------------
# Git workspace port
# ---------------------------------------------------------------------------


class GitWorkspacePort(ABC):
    @abstractmethod
    def create_workspace(self, repo_url: str, task_id: str) -> str:
        """Clone repo and return workspace_path."""
        ...

    @abstractmethod
    def checkout_main_and_create_branch(self, workspace_path: str, branch_name: str) -> None: ...

    @abstractmethod
    def apply_changes_and_commit(self, workspace_path: str, commit_message: str) -> str:
        """Stage all changes, commit, return commit_sha."""
        ...

    @abstractmethod
    def push_branch(
        self, workspace_path: str, branch_name: str, remote_name: str = "origin"
    ) -> None: ...

    @abstractmethod
    def cleanup_workspace(self, workspace_path: str) -> None: ...

    @abstractmethod
    def get_modified_files(self, workspace_path: str) -> list[str]:
        """Return list of relative paths modified since branch creation."""
        ...


# ---------------------------------------------------------------------------
# Agent runtime port
# ---------------------------------------------------------------------------


class SessionHandle(ABC):
    """Opaque handle returned by AgentRuntimePort.start_session."""

    @property
    @abstractmethod
    def session_id(self) -> str: ...


class AgentRuntimePort(ABC):
    @abstractmethod
    def start_session(
        self,
        agent_props: AgentProps,
        workspace_path: str,
        env_vars: dict[str, str],
    ) -> SessionHandle: ...

    @abstractmethod
    def send_execution_payload(
        self,
        handle: SessionHandle,
        context: ExecutionContext,
    ) -> None: ...

    @abstractmethod
    def wait_for_completion(
        self,
        handle: SessionHandle,
        timeout_seconds: int = 600,
    ) -> AgentExecutionResult: ...

    @abstractmethod
    def terminate_session(self, handle: SessionHandle) -> None: ...

    @abstractmethod
    def stream_logs(self, handle: SessionHandle) -> Iterator[str]: ...


# ---------------------------------------------------------------------------
# Auxiliary ports
# ---------------------------------------------------------------------------


class TaskLogsPort(ABC):
    """
    Port for persisting task execution logs and simple metadata.
    Implemented in infra using the filesystem or another storage backend.
    """

    @abstractmethod
    def save_logs(self, task_id: str, result: AgentExecutionResult) -> None: ...


class TestRunnerPort(ABC):
    """
    Port for running acceptance tests against a workspace.
    Application code depends on this instead of subprocess directly.
    """

    @abstractmethod
    def run_tests(self, workspace_path: str, test_command: str) -> None: ...
