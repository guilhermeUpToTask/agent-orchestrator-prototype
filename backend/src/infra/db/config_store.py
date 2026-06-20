"""
src/infra/db/config_store.py — SQLite implementation of ConfigStorePort.

Translates between domain dataclasses and ORM rows (via the table mappers),
enforces optimistic concurrency on ``update_project`` (CAS on state_version),
maps FK violations to ReferentialException, and retries transient
``database is locked`` errors with bounded backoff before surfacing
InfrastructureException.
"""
from __future__ import annotations

from typing import cast

import structlog
from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.app.errors import ResourceNotFoundException
from src.domain.entities.agent_definition import AgentDefinition
from src.domain.entities.model_provider import ModelProvider
from src.domain.entities.project import Project
from src.domain.errors import ConflictException, ReferentialException
from src.domain.repositories.config_store import ConfigStorePort
from src.infra.db._session import run_in_session
from src.infra.db.tables import (
    AgentDefinitionTable,
    ModelProviderTable,
    ProjectTable,
)

log = structlog.get_logger(__name__)


class SqliteConfigStore(ConfigStorePort):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def _run(self, fn):
        return run_in_session(self._sf, fn)

    # -- Projects -------------------------------------------------------------
    def create_project(self, project: Project) -> Project:
        if self.get_project(project.id) is not None:
            raise ConflictException(
                f"Project '{project.id}' already exists",
                code="PROJECT_EXISTS",
                context={"project_id": project.id},
            )

        def _op(s: Session) -> Project:
            s.add(ProjectTable.from_domain(project))
            return project
        try:
            return self._run(_op)
        except IntegrityError as exc:
            # Lost the create race (concurrent insert of the same id).
            raise ConflictException(
                f"Project '{project.id}' already exists",
                code="PROJECT_EXISTS",
                context={"project_id": project.id},
            ) from exc

    def get_project(self, project_id: str) -> Project | None:
        with self._sf() as s:
            row = s.get(ProjectTable, project_id)
            return row.to_domain() if row else None

    def list_projects(self) -> tuple[Project, ...]:
        with self._sf() as s:
            rows = s.execute(select(ProjectTable)).scalars().all()
            return tuple(r.to_domain() for r in rows)

    def update_project(self, project: Project) -> Project:
        def _op(s: Session) -> Project:
            result = cast(CursorResult, s.execute(
                update(ProjectTable)
                .where(
                    ProjectTable.id == project.id,
                    ProjectTable.state_version == project.state_version,
                )
                .values(
                    name=project.name,
                    repo_url=project.repo_url,
                    default_branch=project.default_branch,
                    github_secret_uri=(
                        project.github_secret_ref.uri
                        if project.github_secret_ref else None
                    ),
                    state_version=project.state_version + 1,
                )
            ))
            if result.rowcount == 0:
                existing = s.get(ProjectTable, project.id)
                if existing is None:
                    raise ResourceNotFoundException(
                        f"Project '{project.id}' not found", code="PROJECT_NOT_FOUND"
                    )
                raise ConflictException(
                    f"Project '{project.id}' was modified concurrently",
                    expected_version=project.state_version,
                    actual_version=existing.state_version,
                )
            return project.model_copy(update={"state_version": project.state_version + 1})
        return self._run(_op)

    def delete_project(self, project_id: str, *, cascade: bool = False) -> None:
        def _op(s: Session) -> None:
            s.execute(delete(ProjectTable).where(ProjectTable.id == project_id))
        try:
            self._run(_op)
        except IntegrityError as exc:
            raise ReferentialException(
                f"Project '{project_id}' still has dependents; use cascade",
                context={"project_id": project_id},
            ) from exc

    # -- Agent definitions ----------------------------------------------------
    def upsert_agent(self, agent: AgentDefinition) -> AgentDefinition:
        def _op(s: Session) -> AgentDefinition:
            s.merge(AgentDefinitionTable.from_domain(agent))
            return agent
        try:
            return self._run(_op)
        except IntegrityError as exc:
            raise ReferentialException(
                f"Agent '{agent.id}' references a missing provider "
                f"'{agent.provider_id}'",
                context={"agent_id": agent.id, "provider_id": agent.provider_id},
            ) from exc

    def get_agent(self, agent_id: str) -> AgentDefinition | None:
        with self._sf() as s:
            row = s.get(AgentDefinitionTable, agent_id)
            return row.to_domain() if row else None

    def list_agents(self) -> tuple[AgentDefinition, ...]:
        with self._sf() as s:
            rows = s.execute(select(AgentDefinitionTable)).scalars().all()
            return tuple(r.to_domain() for r in rows)

    def delete_agent(self, agent_id: str) -> None:
        def _op(s: Session) -> None:
            s.execute(delete(AgentDefinitionTable).where(AgentDefinitionTable.id == agent_id))
        self._run(_op)

    # -- Model providers ------------------------------------------------------
    def upsert_provider(self, provider: ModelProvider) -> ModelProvider:
        def _op(s: Session) -> ModelProvider:
            # Replace wholesale so child model rows stay in sync with the
            # domain aggregate (delete-orphan handles removals).
            existing = s.get(ModelProviderTable, provider.id)
            if existing is not None:
                s.delete(existing)
                s.flush()
            s.add(ModelProviderTable.from_domain(provider))
            return provider
        return self._run(_op)

    def get_provider(self, provider_id: str) -> ModelProvider | None:
        with self._sf() as s:
            row = s.get(ModelProviderTable, provider_id)
            return row.to_domain() if row else None

    def list_providers(self) -> tuple[ModelProvider, ...]:
        with self._sf() as s:
            rows = s.execute(select(ModelProviderTable)).scalars().all()
            return tuple(r.to_domain() for r in rows)

    def delete_provider(self, provider_id: str) -> None:
        def _op(s: Session) -> None:
            s.execute(delete(ModelProviderTable).where(ModelProviderTable.id == provider_id))
        try:
            self._run(_op)
        except IntegrityError as exc:
            raise ReferentialException(
                f"Provider '{provider_id}' is still referenced by an agent",
                context={"provider_id": provider_id},
            ) from exc
