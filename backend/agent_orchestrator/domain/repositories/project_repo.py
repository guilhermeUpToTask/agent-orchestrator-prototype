from __future__ import annotations

from typing import Protocol

from agent_orchestrator.domain.entities.project_definition import ProjectDefinition


class ProjectRepository(Protocol):
    def get(self, project_id: str) -> ProjectDefinition: ...

    def list(self) -> list[ProjectDefinition]: ...
