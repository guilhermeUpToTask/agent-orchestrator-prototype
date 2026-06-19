"""
src/api/routers/tasks.py — Task resource endpoints.

Covers:
  GET    /tasks                       list all tasks (read)
  POST   /tasks/{task_id}/retry       force-requeue a task (operator override)
  DELETE /tasks/{task_id}             permanently delete a task record
  DELETE /tasks                       bulk-prune tasks by status
  POST   /tasks/{task_id}/assign      trigger task assignment to an agent
  POST   /tasks/{task_id}/unblock     scan and unblock dependent tasks
  POST   /tasks/{task_id}/fail        trigger fail-handling for a task
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, status

from src.api.dependencies import (
    LogsAdapterDep,
    TaskAssignUseCaseDep,
    TaskDeleteUseCaseDep,
    TaskFailHandlingUseCaseDep,
    TaskPruneUseCaseDep,
    TaskRepoDep,
    TaskRetryUseCaseDep,
    TaskUnblockUseCaseDep,
)
from src.api.schemas.common import ErrorResponse
from src.api.schemas.tasks import (
    TaskAssignResponse,
    TaskDeleteResponse,
    TaskFailHandlingResponse,
    TaskPruneRequest,
    TaskPruneResponse,
    TaskLogsResponse,
    TaskRetryRequest,
    TaskRetryResponse,
    TaskUnblockResponse,
)
from src.domain import TaskStatus

if TYPE_CHECKING:
    from src.domain import TaskLogsPort

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ── Read ──────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[dict],
    summary="List All Tasks",
    description="Returns a lightweight summary of all task records.",
)
def list_tasks(repo: TaskRepoDep) -> list[dict]:
    return [
        {
            "task_id": t.task_id,
            "title": t.title,
            "status": t.status.value,
            "feature_id": t.feature_id,
            "depends_on": t.depends_on,
            "attempt": t.attempt,
        }
        for t in repo.list_all()
    ]


@router.get(
    "/{task_id}/logs",
    response_model=TaskLogsResponse,
    summary="Get Task Console Logs",
    description="Returns the persisted agent stdout/stderr + metadata for a finished task.",
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "No logs for this task."}},
)
def get_task_logs(task_id: str, logs: LogsAdapterDep) -> TaskLogsResponse:
    adapter: "TaskLogsPort" = logs  # type: ignore[assignment]
    data = adapter.read_logs(task_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No logs for task '{task_id}'.")
    return TaskLogsResponse(task_id=task_id, **data)


# ── Retry ─────────────────────────────────────────────────────────────────────

@router.post(
    "/{task_id}/retry",
    response_model=TaskRetryResponse,
    status_code=status.HTTP_200_OK,
    summary="Force-Retry Task",
    description=(
        "Operator override: force-requeue a task regardless of its current status. "
        "The retry counter is **not** incremented — this is an explicit operator action. "
        "Cannot be applied to tasks in `MERGED` status."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Task not found.",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Task is in MERGED status and cannot be requeued.",
        },
    },
)
def retry_task(
    task_id: str,
    payload: TaskRetryRequest,
    use_case: TaskRetryUseCaseDep,
) -> TaskRetryResponse:
    result = use_case.execute(task_id=task_id, actor=payload.actor)
    return TaskRetryResponse(
        task_id=result.task_id,
        previous_status=result.previous_status.value,
    )


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete(
    "/{task_id}",
    response_model=TaskDeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Task",
    description=(
        "Permanently removes a single task record. "
        "Does **not** clean up git branches or Redis leases — "
        "use `POST /project/reset` for a full teardown."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Task not found.",
        }
    },
)
def delete_task(
    task_id: str,
    use_case: TaskDeleteUseCaseDep,
) -> TaskDeleteResponse:
    result = use_case.execute(task_id)
    return TaskDeleteResponse(
        task_id=result.task_id,
        previous_status=result.previous_status.value,
    )


# ── Prune ─────────────────────────────────────────────────────────────────────

@router.delete(
    "",
    response_model=TaskPruneResponse,
    status_code=status.HTTP_200_OK,
    summary="Bulk-Prune Tasks",
    description=(
        "Deletes multiple task records, optionally filtered by status. "
        "When `filter_statuses` is omitted, **all** tasks are deleted. "
        "Valid status values: `created`, `assigned`, `running`, `succeeded`, "
        "`failed`, `canceled`, `merged`."
    ),
    responses={
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "model": ErrorResponse,
            "description": "Unknown status value in filter_statuses.",
        }
    },
)
def prune_tasks(
    payload: TaskPruneRequest,
    use_case: TaskPruneUseCaseDep,
) -> TaskPruneResponse:
    filter_statuses = None
    if payload.filter_statuses is not None:
        filter_statuses = {TaskStatus(s) for s in payload.filter_statuses}

    result = use_case.execute(filter_statuses=filter_statuses)
    return TaskPruneResponse(deleted=result.deleted, count=result.count)


# ── Assign ────────────────────────────────────────────────────────────────────

@router.post(
    "/{task_id}/assign",
    response_model=TaskAssignResponse,
    status_code=status.HTTP_200_OK,
    summary="Assign Task to Agent",
    description=(
        "Triggers the assignment flow for a task: finds an eligible agent, "
        "checks dependencies, and transitions the task to `ASSIGNED`. "
        "Outcome values: `assigned`, `not_assignable`, `no_eligible_agent`, "
        "`deps_not_met`, `not_found`."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Task not found.",
        }
    },
)
def assign_task(
    task_id: str,
    use_case: TaskAssignUseCaseDep,
) -> TaskAssignResponse:
    result = use_case.execute(task_id)
    return TaskAssignResponse(
        task_id=result.task_id,
        outcome=result.outcome.value,
    )


# ── Unblock ───────────────────────────────────────────────────────────────────

@router.post(
    "/{task_id}/unblock",
    response_model=TaskUnblockResponse,
    status_code=status.HTTP_200_OK,
    summary="Unblock Dependent Tasks",
    description=(
        "Scans all tasks that declared `task_id` as a dependency and dispatches "
        "any that are now fully unblocked (all depends_on tasks have succeeded)."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Task not found.",
        }
    },
)
def unblock_tasks(
    task_id: str,
    use_case: TaskUnblockUseCaseDep,
) -> TaskUnblockResponse:
    result = use_case.execute(task_id)
    return TaskUnblockResponse(
        completed_task_id=result.completed_task_id,
        unblocked=result.unblocked,
        skipped=result.skipped,
        count=result.count,
    )


# ── Fail Handling ─────────────────────────────────────────────────────────────

@router.post(
    "/{task_id}/fail",
    response_model=TaskFailHandlingResponse,
    status_code=status.HTTP_200_OK,
    summary="Handle Task Failure",
    description=(
        "Applies the failure-handling policy to a task: requeues if retries remain, "
        "cancels if the retry budget is exhausted. "
        "Outcome values: `requeued`, `canceled`, `skipped`, `not_found`."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Task not found.",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Task state transition violated domain invariants.",
        },
    },
)
def handle_task_failure(
    task_id: str,
    use_case: TaskFailHandlingUseCaseDep,
) -> TaskFailHandlingResponse:
    result = use_case.execute(task_id)
    return TaskFailHandlingResponse(
        task_id=result.task_id,
        outcome=result.outcome.value,
    )
