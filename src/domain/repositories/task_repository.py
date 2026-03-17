"""
src/domain/repositories/task_repository.py — Task persistence port.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.aggregates.task import TaskAggregate


class TaskRepositoryPort(ABC):
    """
    Contract for loading and persisting TaskAggregate instances.

    Uses optimistic concurrency (update_if_version) to prevent lost updates
    when multiple processes — task manager, worker, reconciler — write
    to the same task record concurrently.
    """

    @abstractmethod
    def load(self, task_id: str) -> TaskAggregate:
        """Load task by ID. Raises KeyError if not found."""
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
        """Unconditional save — used for initial creation and operator overrides."""
        ...

    @abstractmethod
    def append_history(
        self, task_id: str, event: str, actor: str, detail: dict
    ) -> None:
        """Append a history entry without bumping state_version."""
        ...

    @abstractmethod
    def list_all(self) -> list[TaskAggregate]:
        """Return all tasks. Used by the reconciler on each sweep."""
        ...

    def get(self, task_id: str) -> "TaskAggregate | None":
        """Return task or None if not found. Default wraps load()."""
        try:
            return self.load(task_id)
        except KeyError:
            return None

    def delete(self, task_id: str) -> bool:
        """Remove the task record. Returns True if deleted, False if not found."""
        return False
