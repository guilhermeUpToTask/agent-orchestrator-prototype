"""
src/api/routers/project.py — Project-level operation endpoints.

Covers:
  POST /project/reset    destructive full project state wipe (operator only)
"""
from __future__ import annotations

from fastapi import APIRouter, status

from src.api.dependencies import ProjectResetUseCaseDep
from src.api.schemas.common import ErrorResponse
from src.api.schemas.project import ProjectResetRequest, ProjectResetResponse

router = APIRouter(prefix="/project", tags=["project"])


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
