"""
src/api/routers/refinement.py — Plan refinement endpoint.

Covers:
  POST /plan/refine     tactical LLM-driven plan refinement session
"""
from __future__ import annotations

from fastapi import APIRouter, status

from src.api.dependencies import RunRefinementUseCaseDep
from src.api.schemas.common import ErrorResponse
from src.api.schemas.refinement import RefineRequest, RefineResponse
from src.api.sse import publish_sse

router = APIRouter(prefix="/plan", tags=["plan"])


@router.post(
    "/refine",
    response_model=RefineResponse,
    status_code=status.HTTP_200_OK,
    summary="Refine Plan",
    description=(
        "Dispatches a user message to the tactical LLM planner for an interactive "
        "refinement session. The planner may add, update, or remove tasks and goals "
        "based on the message. Returns the list of actions taken and whether the "
        "session succeeded. Optionally scoped to a specific node or goal via "
        "`focused_node_id` / `focused_goal_id`."
    ),
    responses={
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "model": ErrorResponse,
            "description": "Context assembly failed (e.g. no project spec loaded).",
        }
    },
)
def refine_plan(
    payload: RefineRequest,
    use_case: RunRefinementUseCaseDep,
) -> RefineResponse:
    result = use_case.execute(
        user_message=payload.message,
        focused_node_id=payload.focused_node_id,
        focused_goal_id=payload.focused_goal_id,
    )
    for action in result.actions_taken:
        publish_sse("plan.refinement_action", {"action": action})

    return RefineResponse(
        session_id=result.session_id,
        actions_taken=result.actions_taken,
        succeeded=result.succeeded,
        error=result.error,
    )
