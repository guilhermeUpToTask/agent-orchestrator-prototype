"""
src/infra/db/tables.py — SQLAlchemy ORM tables for config state.

These are the ORM model family: persistence only. They never leave the infra
layer. ``to_domain()`` / ``from_domain()`` are the *only* place ORM rows are
translated to/from the pure-domain dataclasses — no other module touches a
``*Table`` type.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.domain.entities.agent_definition import AgentDefinition
from src.domain.entities.model_provider import ModelProvider
from src.domain.entities.project import Project
from src.domain.value_objects.config import (
    ProviderKind,
    RegisteredModel,
    SecretRef,
)


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class ProjectTable(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    repo_url: Mapped[str] = mapped_column(String, nullable=False)
    default_branch: Mapped[str] = mapped_column(String, nullable=False, default="main")
    github_secret_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def to_domain(self) -> Project:
        return Project(
            id=self.id,
            name=self.name,
            repo_url=self.repo_url,
            default_branch=self.default_branch,
            github_secret_ref=(
                SecretRef(uri=self.github_secret_uri) if self.github_secret_uri else None
            ),
            created_at=self.created_at,
            state_version=self.state_version,
        )

    @classmethod
    def from_domain(cls, p: Project) -> "ProjectTable":
        return cls(
            id=p.id,
            name=p.name,
            repo_url=p.repo_url,
            default_branch=p.default_branch,
            github_secret_uri=p.github_secret_ref.uri if p.github_secret_ref else None,
            created_at=p.created_at,
            state_version=p.state_version,
        )


# ---------------------------------------------------------------------------
# Model providers + their models
# ---------------------------------------------------------------------------

class ModelProviderTable(Base):
    __tablename__ = "model_providers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    secret_uri: Mapped[str] = mapped_column(String, nullable=False)
    base_url: Mapped[str | None] = mapped_column(String, nullable=True)
    default_model: Mapped[str | None] = mapped_column(String, nullable=True)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    models: Mapped[list["RegisteredModelTable"]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_domain(self) -> ModelProvider:
        return ModelProvider(
            id=self.id,
            kind=ProviderKind(self.kind),
            secret_ref=SecretRef(uri=self.secret_uri),
            base_url=self.base_url,
            default_model=self.default_model,
            models=tuple(m.to_domain() for m in self.models),
            state_version=self.state_version,
        )

    @classmethod
    def from_domain(cls, p: ModelProvider) -> "ModelProviderTable":
        row = cls(
            id=p.id,
            kind=p.kind.value,
            secret_uri=p.secret_ref.uri,
            base_url=p.base_url,
            default_model=p.default_model,
            state_version=p.state_version,
        )
        row.models = [RegisteredModelTable.from_domain(p.id, m) for m in p.models]
        return row


class RegisteredModelTable(Base):
    __tablename__ = "registered_models"
    __table_args__ = (UniqueConstraint("provider_id", "model_id", name="uq_provider_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("model_providers.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    provider: Mapped[ModelProviderTable] = relationship(back_populates="models")

    def to_domain(self) -> RegisteredModel:
        return RegisteredModel(
            model_id=self.model_id,
            display_name=self.display_name,
            capabilities=tuple(self.capabilities or ()),
        )

    @classmethod
    def from_domain(cls, provider_id: str, m: RegisteredModel) -> "RegisteredModelTable":
        return cls(
            provider_id=provider_id,
            model_id=m.model_id,
            display_name=m.display_name,
            capabilities=list(m.capabilities),
        )


# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------

class AgentDefinitionTable(Base):
    __tablename__ = "agent_definitions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    runtime_type: Mapped[str] = mapped_column(String, nullable=False)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("model_providers.id", ondelete="RESTRICT"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def to_domain(self) -> AgentDefinition:
        return AgentDefinition(
            id=self.id,
            name=self.name,
            capabilities=tuple(self.capabilities or ()),
            runtime_type=self.runtime_type,
            provider_id=self.provider_id,
            model_id=self.model_id,
            state_version=self.state_version,
        )

    @classmethod
    def from_domain(cls, a: AgentDefinition) -> "AgentDefinitionTable":
        return cls(
            id=a.id,
            name=a.name,
            capabilities=list(a.capabilities),
            runtime_type=a.runtime_type,
            provider_id=a.provider_id,
            model_id=a.model_id,
            state_version=a.state_version,
        )


# ---------------------------------------------------------------------------
# Secrets (ciphertext only)
# ---------------------------------------------------------------------------

class SecretTable(Base):
    __tablename__ = "secrets"

    uri: Mapped[str] = mapped_column(String, primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    wrapped_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )


# ---------------------------------------------------------------------------
# Active project (per-session selection)
# ---------------------------------------------------------------------------

class ActiveProjectTable(Base):
    __tablename__ = "active_projects"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False)


# ---------------------------------------------------------------------------
# Task state (Stage B) — the full aggregate is stored as JSON in ``data``;
# scalar columns are projections for querying (reconciler) and CAS.
# ---------------------------------------------------------------------------

class TaskTable(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    # Nullable + FK: NULL skips the FK check (back-compat with project-less
    # tasks); a set project_id gives delete_project its cascade backstop.
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class TaskTransitionTable(Base):
    """Append-only audit of task transitions (one row per recorded history entry)."""

    __tablename__ = "task_transitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False, default="")
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
