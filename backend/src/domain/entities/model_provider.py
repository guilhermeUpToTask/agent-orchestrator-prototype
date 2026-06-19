"""
src/domain/entities/model_provider.py — ModelProvider entity.

A ModelProvider is a GLOBAL entity (not scoped to any project): it names a
provider account (Anthropic, OpenAI, …), points at its API key via a SecretRef,
and lists the models it offers. Projects reference providers by id; they never
own them.

``state_version`` supports optimistic concurrency at the store boundary.
"""
from __future__ import annotations

from pydantic import BaseModel

from src.domain.value_objects.config import ProviderKind, RegisteredModel, SecretRef


class ModelProvider(BaseModel):
    id: str
    kind: ProviderKind
    secret_ref: SecretRef
    models: tuple[RegisteredModel, ...] = ()
    base_url: str | None = None
    default_model: str | None = None
    state_version: int = 0

    def with_model(self, model: RegisteredModel) -> "ModelProvider":
        """Return a copy with ``model`` added (replacing any same model_id)."""
        kept = tuple(m for m in self.models if m.model_id != model.model_id)
        return self.model_copy(update={"models": (*kept, model)})

    def has_model(self, model_id: str) -> bool:
        return any(m.model_id == model_id for m in self.models)
