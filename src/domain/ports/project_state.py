"""
src/domain/ports/project_state.py — Project state persistence port.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DecisionEntry:
    """Immutable architectural decision record."""
    id: str
    date: str
    status: str           # "active" | "superseded"
    domain: str
    feature_tag: str      # "" for global decisions
    content: str
    superseded_by: Optional[str] = None


class ProjectStatePort(ABC):
    """Contract for reading and writing planner-owned project state documents."""

    @abstractmethod
    def read_state(self, key: str) -> Optional[str]: ...

    @abstractmethod
    def write_state(self, key: str, content: str) -> None: ...

    @abstractmethod
    def list_keys(self) -> list[str]: ...

    @abstractmethod
    def delete_state(self, key: str) -> bool: ...

    @abstractmethod
    def write_decision(self, entry: DecisionEntry) -> None: ...

    @abstractmethod
    def list_decisions(
        self,
        domain: Optional[str] = None,
        status: str = "active",
    ) -> list[DecisionEntry]: ...

    @abstractmethod
    def supersede_decision(
        self,
        id: str,
        superseded_by: str,
        reason: str,
    ) -> bool: ...
