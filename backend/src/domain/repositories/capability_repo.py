from __future__ import annotations

from typing import Protocol

from src.domain.entities.capability import Capability


class CapabilityRepository(Protocol):
    """Capabilities have their own identity and will grow tooling relationships.
    User-managed at runtime; delete() guarded if referenced by an agent or task."""

    def get(self, capability_id: str) -> Capability: ...
    def list(self) -> list[Capability]: ...
    def add(self, capability: Capability) -> None: ...
    def update(self, capability: Capability) -> None: ...
    def delete(self, capability_id: str) -> None: ...  # guarded
