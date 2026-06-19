"""
src/domain/repositories/capability_registry.py — Capability registry port.

The registry is the dynamic source of truth for which capability tags exist.
Seeded with built-in defaults, extensible at runtime (CLI + API). Boundaries
(agent registration, task creation) validate tags against it so a typo cannot
silently produce an agent or task that matches nothing.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class CapabilityRegistryPort(ABC):
    """Contract for the known set of capability tags."""

    @abstractmethod
    def list_tags(self) -> list[str]:
        """Return all registered capability tags, sorted."""
        ...

    @abstractmethod
    def add(self, tag: str) -> None:
        """Register a new capability tag (normalized). Idempotent."""
        ...

    @abstractmethod
    def remove(self, tag: str) -> None:
        """Remove a capability tag. No-op if it is not registered."""
        ...

    @abstractmethod
    def exists(self, tag: str) -> bool:
        """Return True if the (normalized) tag is registered."""
        ...

    @abstractmethod
    def ensure_defaults(self, defaults: list[str]) -> None:
        """Register any missing built-in defaults without removing existing tags."""
        ...
