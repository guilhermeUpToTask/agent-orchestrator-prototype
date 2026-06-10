"""
src/api/routers/goals.py — Goal resource endpoints.

Covers:
  GET  /goals                        list all goals
  GET  /goals/{goal_id}              get a single goal
  GET  /goals/{goal_id}/history      goal history log
  POST /goals/{goal_id}/finalize     operator finalizes an approved/merged goal
  POST /goals/{goal_id}/unblock      manually trigger goal unblocking
  POST /goals/{goal_id}/pr/create    open GitHub PR for a ready-for-review goal
  POST /goals/{goal_id}/pr/sync      sync PR status from GitHub
  POST /goals/{goal_id}/pr/advance   apply PR-driven state transitions
"""
from __future__ import annotations

from fastapi import APIRouter, status

from src.api.dependencies import (
    AdvanceGoalFromPRUseCaseDep,
    CreateGoalPRUseCaseDep,
    GoalFinalizeUseCaseDep,
    GoalRepoDep,
    SyncGoalPRUseCaseDep,
    UnblockGoalsUseCaseDep,
)
from src.api.schemas.common import ErrorResponse
from src.api.schemas.goals import (
    GoalFinalizeResponse,
    GoalHistoryEntryResponse,
    GoalResponse,
    GoalTaskResponse,
)
from src.api.sse import publish_sse

router = APIRouter(prefix="/goals", tags=["goals"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _goal_to_response(goal) -> GoalResponse:
    tasks = [
        GoalTaskResponse(
            task_id=t.task_id,
            title=t.title,
            status=t.status.value,
            depends_on=t.depends_on,
        )
        for t in goal.tasks.values()
    ]
    history = [GoalHistoryEntryResponse(**h.model_dump()) for h in goal.history]
    return GoalResponse(
        goal_id=goal.goal_id,
        name=goal.name,
        description=goal.description,
        status=goal.status.value,
        feature_tag=goal.feature_tag,
        depends_on=goal.depends_on,
        tasks=tasks,
        history=history,
        pr_number=goal.pr_number,
        pr_status=goal.pr_status,
        pr_html_url=goal.pr_html_url,
        pr_checks_passed=goal.pr_checks_passed,
        pr_approved=goal.pr_approved,
    )


# ── Read ──────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[GoalResponse],
    summary="List All Goals",
    description="Returns all goal aggregates with their tasks and current status.",
)
def list_goals(repo: GoalRepoDep) -> list[GoalResponse]:
    return [_goal_to_response(g) for g in repo.list_all()]


@router.get(
    "/{goal_id}",
    response_model=GoalResponse,
    summary="Get Goal",
    description="Fetches the current state of a goal aggregate by its ID.",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Goal not found.",
        }
    },
)
def get_goal(goal_id: str, repo: GoalRepoDep) -> GoalResponse:
    goal = repo.load(goal_id)  # raises KeyError → 404 via global handler
    return _goal_to_response(goal)


@router.get(
    "/{goal_id}/history",
    response_model=list[GoalHistoryEntryResponse],
    summary="Get Goal History",
    description="Returns the ordered history log of state transitions for a goal.",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Goal not found.",
        }
    },
)
def get_goal_history(goal_id: str, repo: GoalRepoDep) -> list[GoalHistoryEntryResponse]:
    goal = repo.load(goal_id)  # raises KeyError → 404
    return [GoalHistoryEntryResponse(**h.model_dump()) for h in goal.history]


# ── Finalize ──────────────────────────────────────────────────────────────────

@router.post(
    "/{goal_id}/finalize",
    response_model=GoalFinalizeResponse,
    status_code=status.HTTP_200_OK,
    summary="Finalize Goal",
    description=(
        "Operator finalizes a goal that is in `APPROVED` or `MERGED` status. "
        "Records the finalization event; does **not** perform any git operations — "
        "the GitHub PR merge must happen through the GitHub UI."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Goal not found.",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Goal is not in APPROVED or MERGED status, or already finalized.",
        },
    },
)
def finalize_goal(
    goal_id: str,
    use_case: GoalFinalizeUseCaseDep,
) -> GoalFinalizeResponse:
    result = use_case.execute(goal_id)  # returns dict; raises ValueError → 409
    publish_sse("goal.finalized", {"goal_id": goal_id})
    return GoalFinalizeResponse(
        goal_id=result["goal_id"],
        pr_number=result.get("pr_number"),
        pr_url=result.get("pr_url"),
        goal_status=result["goal_status"],
    )


# ── Unblock ───────────────────────────────────────────────────────────────────

@router.post(
    "/{goal_id}/unblock",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Unblock Dependent Goals",
    description=(
        "Scans all PENDING goals that declared this goal as a dependency and "
        "starts any that are now fully unblocked (all prerequisites merged)."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Goal not found.",
        }
    },
)
def unblock_goals(
    goal_id: str,
    repo: GoalRepoDep,
    use_case: UnblockGoalsUseCaseDep,
) -> dict:
    # Verify goal exists first
    goal = repo.load(goal_id)  # raises KeyError → 404
    result = use_case.execute(goal.name)
    return {
        "merged_goal_name": result.merged_goal_name,
        "unblocked": result.unblocked,
        "still_blocked": result.still_blocked,
        "count": result.count,
    }


# ── PR Lifecycle ──────────────────────────────────────────────────────────────

@router.post(
    "/{goal_id}/pr/create",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Create GitHub PR for Goal",
    description=(
        "Opens a GitHub PR for a goal that has reached `READY_FOR_REVIEW` status. "
        "Idempotent — re-calling returns the existing PR number."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Goal not found.",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Goal is not in READY_FOR_REVIEW status.",
        },
    },
)
def create_goal_pr(
    goal_id: str,
    use_case: CreateGoalPRUseCaseDep,
) -> dict:
    pr_number = use_case.execute(goal_id)
    publish_sse("goal.pr_opened", {"goal_id": goal_id, "pr_number": pr_number})
    return {"goal_id": goal_id, "pr_number": pr_number}


@router.post(
    "/{goal_id}/pr/sync",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Sync Goal PR Status from GitHub",
    description=(
        "Polls GitHub for the current PR state and syncs it into the goal aggregate. "
        "Only operates on goals in `AWAITING_PR_APPROVAL` or `APPROVED` status."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Goal not found.",
        }
    },
)
def sync_goal_pr_status(
    goal_id: str,
    use_case: SyncGoalPRUseCaseDep,
) -> dict:
    use_case.execute(goal_id)
    publish_sse("goal.pr_state_synced", {"goal_id": goal_id})
    return {"goal_id": goal_id, "synced": True}


@router.post(
    "/{goal_id}/pr/advance",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Advance Goal from PR State",
    description=(
        "Applies eligible PR-driven state transitions (e.g. AWAITING_PR_APPROVAL → APPROVED). "
        "Should be called after `/pr/sync`."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Goal not found.",
        }
    },
)
def advance_goal_from_pr(
    goal_id: str,
    use_case: AdvanceGoalFromPRUseCaseDep,
) -> dict:
    use_case.execute(goal_id)
    return {"goal_id": goal_id, "advanced": True}
