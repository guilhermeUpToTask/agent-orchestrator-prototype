"""
src/domain/repositories/planner_session_repository.py — PlannerSession persistence port.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.aggregates.planner_session import PlannerSession


class PlannerSessionRepositoryPort(ABC):

    @abstractmethod
    def save(self, session: PlannerSession) -> None:
        """Unconditional save — used for initial creation and all updates."""
        ...

    @abstractmethod
    def load(self, session_id: str) -> PlannerSession:
        """Load session by ID. Raises KeyError if not found."""
        ...

    @abstractmethod
    def list_all(self) -> list[PlannerSession]:
        """Return all sessions, newest first."""
        ...

    def get(self, session_id: str) -> Optional[PlannerSession]:
        """Return session or None if not found."""
        try:
            return self.load(session_id)
        except KeyError:
            return None
