"""
src/domain/repositories/goal_repository.py — Goal persistence port.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.aggregates.goal import GoalAggregate


class GoalRepositoryPort(ABC):
    """
    Contract for loading and persisting GoalAggregate instances.

    Uses the same optimistic concurrency pattern as TaskRepositoryPort:
    update_if_version returns False on a version conflict rather than raising,
    letting the caller decide whether to reload and retry.
    """

    @abstractmethod
    def save(self, goal: GoalAggregate) -> None:
        """Unconditional save — used for initial creation."""
        ...

    @abstractmethod
    def load(self, goal_id: str) -> GoalAggregate:
        """Load goal by ID. Raises KeyError if not found."""
        ...

    @abstractmethod
    def update_if_version(
        self,
        goal_id: str,
        new_state: GoalAggregate,
        expected_version: int,
    ) -> bool:
        """
        Atomic compare-and-swap write.
        Returns True on success, False on version conflict.
        """
        ...

    @abstractmethod
    def list_all(self) -> list[GoalAggregate]:
        """Return all persisted goals."""
        ...

    def get(self, goal_id: str) -> Optional[GoalAggregate]:
        """Return goal or None if not found."""
        try:
            return self.load(goal_id)
        except KeyError:
            return None
