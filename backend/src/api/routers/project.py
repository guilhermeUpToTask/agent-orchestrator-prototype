"""
src/api/routers/project.py — Project-level operation endpoints.

Covers:
  GET  /project/context  active project name + mode resolved from config.json
  POST /project/reset    destructive full project state wipe (operator only)
"""
from __future__ import annotations

from fastapi import APIRouter, status

from src.api.dependencies import (
    ProjectNameDep,
    ProjectResetUseCaseDep,
    SettingsContextDep,
)
from src.api.schemas.common import ErrorResponse
from src.api.schemas.project import (
    ProjectContextResponse,
    ProjectResetRequest,
    ProjectResetResponse,
)

router = APIRouter(prefix="/project", tags=["project"])


@router.get(
    "/context",
    response_model=ProjectContextResponse,
    summary="Get Active Project Context",
    description=(
        "Returns the project name and mode the API is currently scoped to, "
        "as resolved from `.orchestrator/config.json` and the environment."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "No active project is configured.",
        }
    },
)
def get_project_context(
    project_name: ProjectNameDep,
    ctx: SettingsContextDep,
) -> ProjectContextResponse:
    return ProjectContextResponse(project_name=project_name, mode=ctx.mode)


@router.post(
    "/reset",
    response_model=ProjectResetResponse,
    status_code=status.HTTP_200_OK,
    summary="Reset Project State",
    description=(
        "**Destructive operator action.** Wipes all tasks, Redis leases, and "
        "git branches matching the `task-<id>` naming pattern. "
        "Set `keep_agents=true` to preserve the agent registry across the reset. "
        "Each step is attempted independently; failures are collected in `errors` "
        "rather than aborting the remaining steps."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Reset blocked by a domain invariant.",
        }
    },
)
def reset_project(
    payload: ProjectResetRequest,
    use_case: ProjectResetUseCaseDep,
) -> ProjectResetResponse:
    result = use_case.execute(keep_agents=payload.keep_agents)
    return ProjectResetResponse(
        tasks_deleted=result.tasks_deleted,
        leases_released=result.leases_released,
        branches_deleted=result.branches_deleted,
        agents_removed=result.agents_removed,
        had_errors=result.had_errors,
        errors=result.errors,
    )
