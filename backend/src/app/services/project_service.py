"""
src/app/services/project_service.py — Project application service.

Thin orchestration over ConfigStorePort + SecretStorePort + ActiveProjectPort,
shared by the CLI and the API. Holds no persistence detail; raises the shared
domain/app exception taxonomy only (never HTTP).
"""
from __future__ import annotations

import re

import structlog

from src.app.errors import ResourceNotFoundException, ValidationException
from src.domain.entities.project import Project
from src.domain.ports.active_project import ActiveProjectPort
from src.domain.repositories.config_store import ConfigStorePort
from src.domain.repositories.secret_store import SecretStorePort
from src.domain.value_objects.config import SecretRef

log = structlog.get_logger(__name__)


def slugify(value: str) -> str:
    """Lower-case, hyphenate, strip to a stable id slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        raise ValidationException("Cannot derive an id from an empty name", code="VALIDATION_ERROR")
    return slug


class ProjectService:
    def __init__(
        self,
        config_store: ConfigStorePort,
        secret_store: SecretStorePort,
        active_project: ActiveProjectPort,
    ) -> None:
        self._config = config_store
        self._secrets = secret_store
        self._active = active_project

    def create_project(
        self,
        *,
        name: str,
        repo_url: str,
        default_branch: str = "main",
        github_token: str | None = None,
        project_id: str | None = None,
    ) -> Project:
        pid = project_id or slugify(name)
        github_ref: SecretRef | None = None
        if github_token:
            github_ref = SecretRef.for_project_github(pid)
            self._secrets.put(github_ref, github_token)
        project = Project(
            id=pid,
            name=name,
            repo_url=repo_url,
            default_branch=default_branch,
            github_secret_ref=github_ref,
        )
        created = self._config.create_project(project)
        log.info("project.created", project_id=created.id, name=created.name)
        return created

    def get_project(self, project_id: str) -> Project:
        project = self._config.get_project(project_id)
        if project is None:
            raise ResourceNotFoundException(
                f"Project '{project_id}' not found", code="PROJECT_NOT_FOUND"
            )
        return project

    def list_projects(self) -> tuple[Project, ...]:
        return self._config.list_projects()

    def delete_project(self, project_id: str, *, cascade: bool = False) -> None:
        # Resolve first so a missing project is a clean 404, not a store error.
        self.get_project(project_id)
        self._config.delete_project(project_id, cascade=cascade)
        log.info("project.deleted", project_id=project_id, cascade=cascade)

    def activate(self, session_id: str, project_id: str) -> Project:
        project = self.get_project(project_id)
        self._active.set_active(session_id, project_id)
        log.info("project.activated", session_id=session_id, project_id=project_id)
        return project

    def get_active(self, session_id: str) -> Project | None:
        pid = self._active.get_active(session_id)
        if pid is None:
            return None
        return self._config.get_project(pid)
