"""
src/api/routers/control_projects.py — project control-plane endpoints.

Thin: validate (Pydantic) -> call ProjectService -> return a response DTO.
No business logic, no try/except, no logging here (all centralized).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, status

from src.api.dependencies import ProjectServiceDep
from src.api.schemas.control import ProjectCreateRequest, ProjectResponse
from src.api.security import require_api_token

if TYPE_CHECKING:
    from src.app.services.project_service import ProjectService

# Session under which the API tracks the active project (prototype: one session).
API_SESSION = "api"

router = APIRouter(
    prefix="/projects",
    tags=["control-projects"],
    dependencies=[Depends(require_api_token)],
)


@router.get("", response_model=list[ProjectResponse], summary="List Projects")
def list_projects(svc: ProjectServiceDep) -> list[ProjectResponse]:
    service: "ProjectService" = svc  # type: ignore[assignment]
    return [ProjectResponse.from_domain(p) for p in service.list_projects()]


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Project",
)
def create_project(body: ProjectCreateRequest, svc: ProjectServiceDep) -> ProjectResponse:
    service: "ProjectService" = svc  # type: ignore[assignment]
    project = service.create_project(
        name=body.name,
        repo_url=body.repo_url,
        default_branch=body.default_branch,
        github_token=body.github_token,
        project_id=body.project_id,
    )
    return ProjectResponse.from_domain(project)


@router.post(
    "/{project_id}/activate",
    response_model=ProjectResponse,
    summary="Activate Project",
)
def activate_project(project_id: str, svc: ProjectServiceDep) -> ProjectResponse:
    service: "ProjectService" = svc  # type: ignore[assignment]
    return ProjectResponse.from_domain(service.activate(API_SESSION, project_id))


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Project",
)
def delete_project(project_id: str, svc: ProjectServiceDep, cascade: bool = False) -> None:
    service: "ProjectService" = svc  # type: ignore[assignment]
    service.delete_project(project_id, cascade=cascade)
