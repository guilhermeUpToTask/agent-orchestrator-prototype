"""
src/domain/value_objects/config.py — Config-layer value objects.

Immutable descriptors used by the global configuration entities (providers,
agent definitions, projects). They own their validation and hold no mutable
identity of their own.

  ProviderKind     — which model-provider family a provider belongs to.
  SecretRef        — an opaque pointer to a stored secret (never the plaintext).
  RegisteredModel  — a model offered by a provider.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator


class ProviderKind(str, Enum):
    ANTHROPIC  = "anthropic"
    GEMINI     = "gemini"
    OPENROUTER = "openrouter"
    OPENAI     = "openai"


class SecretRef(BaseModel):
    """
    A reference to a secret stored in the secret store. Carries only the URI
    (``secret://provider/<id>``); the plaintext is never held here and is
    resolved exclusively by the secret adapter.
    """

    model_config = ConfigDict(frozen=True)

    uri: str

    @field_validator("uri")
    @classmethod
    def _must_be_secret_uri(cls, v: str) -> str:
        if not v.startswith("secret://"):
            raise ValueError("SecretRef must start with 'secret://'")
        return v

    @classmethod
    def for_provider(cls, provider_id: str) -> "SecretRef":
        """Canonical ref for a provider's API key."""
        return cls(uri=f"secret://provider/{provider_id}")

    @classmethod
    def for_project_github(cls, project_id: str) -> "SecretRef":
        """Canonical ref for a project's GitHub token."""
        return cls(uri=f"secret://project/{project_id}/github")


class RegisteredModel(BaseModel):
    """A model offered by a provider, with optional capability tags."""

    model_config = ConfigDict(frozen=True)

    model_id: str
    display_name: str
    capabilities: tuple[str, ...] = ()
