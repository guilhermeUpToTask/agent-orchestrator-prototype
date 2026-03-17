"""
src/domain/ports/storage.py — Auxiliary storage ports.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.value_objects.execution import AgentExecutionResult


class TaskLogsPort(ABC):
    """
    Contract for persisting agent execution logs.
    Infrastructure provides adapters (filesystem, object storage, etc.).
    """

    @abstractmethod
    def save_logs(self, task_id: str, result: AgentExecutionResult) -> None:
        """Persist stdout, stderr, and metadata from an agent execution."""
        ...


class TestRunnerPort(ABC):
    """
    Contract for running acceptance tests inside a workspace.
    Wraps subprocess invocation so the application layer never calls subprocess directly.
    """

    @abstractmethod
    def run_tests(self, workspace_path: str, test_command: str) -> None:
        """
        Execute the test command inside workspace_path.
        Raises on non-zero exit so the worker can treat it as a failure.
        """
        ...
