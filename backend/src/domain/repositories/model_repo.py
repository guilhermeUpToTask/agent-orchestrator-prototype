from __future__ import annotations

from typing import Protocol

from src.domain.entities.ia_model import IAModel


class ModelRepository(Protocol):
    """User-managed at runtime. delete() guarded if referenced by an active agent."""

    def get(self, model_id: str) -> IAModel: ...
    # list_by_provider declared BEFORE list: the `list` method name shadows the
    # builtin inside the class body, breaking later `list[IAModel]` annotations.
    def list_by_provider(self, provider_id: str) -> list[IAModel]: ...
    def list(self) -> list[IAModel]: ...
    def add(self, model: IAModel) -> None: ...
    def update(self, model: IAModel) -> None: ...
    def delete(self, model_id: str) -> None: ...  # guarded
