from __future__ import annotations

from typing import Protocol

from src.domain.entities.model_provider import ModelProvider


class ModelProviderRepository(Protocol):
    """User-managed at runtime. delete() CASCADES to the provider's models
    (provider owns its models), but is GUARDED against deletion if any of its
    models is referenced by an active agent (cascade down, guard up)."""

    def get(self, provider_id: str) -> ModelProvider: ...
    def list(self) -> list[ModelProvider]: ...
    def add(self, provider: ModelProvider) -> None: ...
    def update(self, provider: ModelProvider) -> None: ...
    def delete(
        self, provider_id: str
    ) -> None: ...  # cascade to models, guard if in use
