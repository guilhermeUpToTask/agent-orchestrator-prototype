"""
src/domain/ports/project_state.py — Project state persistence port.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from src.domain.project_spec.aggregate import ProjectSpec


@dataclass(frozen=True)
class SpecChanges:
    """Structured spec changes that can be applied to a ProjectSpec."""
    add_required: list[str] = field(default_factory=list)
    add_forbidden: list[str] = field(default_factory=list)
    remove_required: list[str] = field(default_factory=list)
    remove_forbidden: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any([
            self.add_required, self.add_forbidden,
            self.remove_required, self.remove_forbidden,
        ])


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
    spec_changes: Optional[SpecChanges] = None  # None means no spec impact


def apply_to_spec(spec: ProjectSpec, decision: DecisionEntry) -> ProjectSpec:
    """
    Apply spec_changes from a DecisionEntry to a ProjectSpec.

    Returns a new spec with the changes applied. Does not write to disk —
    the caller does that atomically.

    Raises ValueError if spec_changes is None or empty.
    """
    if decision.spec_changes is None:
        raise ValueError(
            f"Decision '{decision.id}' has no spec_changes to apply"
        )
    if decision.spec_changes.is_empty:
        raise ValueError(
            f"Decision '{decision.id}' has empty spec_changes"
        )

    sc = decision.spec_changes
    return spec._apply_approved_change(
        add_required=sc.add_required,
        remove_required=sc.remove_required,
        add_forbidden=sc.add_forbidden,
        remove_forbidden=sc.remove_forbidden,
    )


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
