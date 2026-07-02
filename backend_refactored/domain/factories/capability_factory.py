from __future__ import annotations

from typing import Any

from domain.entities.capability import Capability
from domain.factories.identity import new_id


# Thin, but kept for consistency with the other factories: create() centralizes id
# generation (new_id()) and mirrors the uniform create()/reconstruct() split.
class CapabilityFactory:
    @staticmethod
    def create(
        name: str, description: str, tools: list[str] | None = None
    ) -> Capability:
        return Capability(
            id=new_id(), name=name, description=description, tools=tools or []
        )

    @staticmethod
    def reconstruct(data: dict[str, Any]) -> Capability:
        return Capability.model_validate(data)
