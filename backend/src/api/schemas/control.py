"""
src/api/schemas/control.py — control-plane request/response DTOs.

The third model family: Pydantic serialization only. They map to/from the
domain dataclasses at the router boundary; no domain or ORM type is exposed.
Secrets are write-only — they appear in *Create requests but never in responses.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from src.domain.entities.agent_definition import AgentDefinition
from src.domain.entities.model_provider import ModelProvider
from src.domain.entities.project import Project
from src.domain.value_objects.config import ProviderKind


# ── Projects ────────────────────────────────────────────────────────────────

class ProjectCreateRequest(BaseModel):
    name: str
    repo_url: str
    default_branch: str = "main"
    github_token: str | None = None  # write-only; stored encrypted
    project_id: str | None = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    repo_url: str
    default_branch: str
    has_github_token: bool
    state_version: int

    @classmethod
    def from_domain(cls, p: Project) -> "ProjectResponse":
        return cls(
            id=p.id,
            name=p.name,
            repo_url=p.repo_url,
            default_branch=p.default_branch,
            has_github_token=p.github_secret_ref is not None,
            state_version=p.state_version,
        )


# ── Providers + models ──────────────────────────────────────────────────────

class ProviderCreateRequest(BaseModel):
    id: str
    kind: ProviderKind
    api_key: str  # write-only; stored encrypted
    base_url: str | None = None
    default_model: str | None = None


class ModelResponse(BaseModel):
    model_id: str
    display_name: str
    capabilities: list[str] = Field(default_factory=list)


class ProviderResponse(BaseModel):
    id: str
    kind: ProviderKind
    base_url: str | None
    default_model: str | None
    models: list[ModelResponse]
    state_version: int

    @classmethod
    def from_domain(cls, p: ModelProvider) -> "ProviderResponse":
        return cls(
            id=p.id,
            kind=p.kind,
            base_url=p.base_url,
            default_model=p.default_model,
            models=[
                ModelResponse(
                    model_id=m.model_id,
                    display_name=m.display_name,
                    capabilities=list(m.capabilities),
                )
                for m in p.models
            ],
            state_version=p.state_version,
        )


class ModelCreateRequest(BaseModel):
    model_id: str
    display_name: str | None = None
    capabilities: list[str] = Field(default_factory=list)


# ── Agent definitions ───────────────────────────────────────────────────────

class AgentDefinitionCreateRequest(BaseModel):
    id: str
    name: str
    runtime_type: str
    provider_id: str
    model_id: str
    capabilities: list[str] = Field(default_factory=list)


class AgentDefinitionResponse(BaseModel):
    id: str
    name: str
    runtime_type: str
    provider_id: str
    model_id: str
    capabilities: list[str]
    state_version: int

    @classmethod
    def from_domain(cls, a: AgentDefinition) -> "AgentDefinitionResponse":
        return cls(
            id=a.id,
            name=a.name,
            runtime_type=a.runtime_type,
            provider_id=a.provider_id,
            model_id=a.model_id,
            capabilities=list(a.capabilities),
            state_version=a.state_version,
        )


# ── Secrets ─────────────────────────────────────────────────────────────────

class SecretCreateRequest(BaseModel):
    uri: str
    value: str  # write-only


class SecretRefResponse(BaseModel):
    """Masked metadata only — never the value."""

    uri: str
    is_set: bool = True
