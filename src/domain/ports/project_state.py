"""
src/domain/ports/project_state.py — Project state persistence port.

ProjectState is the planner's persistent memory across sessions.  It stores
accumulated architectural decisions, the current architecture description, and
any other free-form context the planner needs to remain coherent over time.

Design:
  - Keys are short human-readable identifiers: "decisions", "current_arch",
    "context", or any name the planner chooses.
  - Values are plain strings (markdown prose).
  - No schema enforcement — the port is deliberately thin.  The planner is
    responsible for the content; the port is responsible for durability.
  - read_state returns None for a key that has never been written, so callers
    can distinguish "no decisions yet" from an empty string.

Storage layout (filesystem adapter):
  ~/.orchestrator/<project>/project_state/<key>.md
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class ProjectStatePort(ABC):
    """
    Contract for reading and writing planner-owned project state documents.

    Infrastructure provides a filesystem adapter.  Tests can use an in-memory
    adapter without touching disk.
    """

    @abstractmethod
    def read_state(self, key: str) -> Optional[str]:
        """
        Return the stored content for *key*, or None if it has never been set.
        """
        ...

    @abstractmethod
    def write_state(self, key: str, content: str) -> None:
        """
        Persist *content* under *key*, replacing any previous value.
        Writes are atomic (temp-file + rename) so a crash mid-write never
        leaves a partially-written document.
        """
        ...

    @abstractmethod
    def list_keys(self) -> list[str]:
        """Return the names of all keys that currently have stored content."""
        ...

    @abstractmethod
    def delete_state(self, key: str) -> bool:
        """
        Remove the stored content for *key*.
        Returns True if the key existed, False if it was already absent.
        """
        ...
