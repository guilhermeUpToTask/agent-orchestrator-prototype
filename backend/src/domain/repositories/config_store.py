"""
src/domain/repositories/config_store.py — Config persistence port.

Single contract for the GLOBAL configuration entities (projects, agent
definitions, model providers). Implementations use optimistic concurrency via
``state_version`` and must:

  * raise ConflictException when a CAS write finds a stale state_version;
  * raise ReferentialException when a delete would orphan referencing rows, or
    an upsert references a missing parent.

The domain knows nothing about SQL; the SQLite/SQLAlchemy adapter implements
this in the infra layer.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.entities.agent_definition import AgentDefinition
from src.domain.entities.model_provider import ModelProvider
from src.domain.entities.project import Project


class ConfigStorePort(ABC):
    # -- Projects -------------------------------------------------------------
    @abstractmethod
    def create_project(self, project: Project) -> Project:
        ...

    @abstractmethod
    def get_project(self, project_id: str) -> Project | None:
        ...

    @abstractmethod
    def list_projects(self) -> tuple[Project, ...]:
        ...

    @abstractmethod
    def update_project(self, project: Project) -> Project:
        """CAS on state_version. Raises ConflictException on mismatch."""
        ...

    @abstractmethod
    def delete_project(self, project_id: str, *, cascade: bool = False) -> None:
        """Delete a project. Raises ReferentialException if it still owns tasks
        and ``cascade`` is False."""
        ...

    # -- Agent definitions (global) ------------------------------------------
    @abstractmethod
    def upsert_agent(self, agent: AgentDefinition) -> AgentDefinition:
        ...

    @abstractmethod
    def get_agent(self, agent_id: str) -> AgentDefinition | None:
        ...

    @abstractmethod
    def list_agents(self) -> tuple[AgentDefinition, ...]:
        ...

    @abstractmethod
    def delete_agent(self, agent_id: str) -> None:
        ...

    # -- Model providers (global) --------------------------------------------
    @abstractmethod
    def upsert_provider(self, provider: ModelProvider) -> ModelProvider:
        ...

    @abstractmethod
    def get_provider(self, provider_id: str) -> ModelProvider | None:
        ...

    @abstractmethod
    def list_providers(self) -> tuple[ModelProvider, ...]:
        ...

    @abstractmethod
    def delete_provider(self, provider_id: str) -> None:
        """Raises ReferentialException if an agent still references it."""
        ...
