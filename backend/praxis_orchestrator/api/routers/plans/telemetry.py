"""What actually happened: planning artifacts, the attempt timeline, attempt
logs (including the live tail) and the fine-grained agent event feed."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from praxis_orchestrator.api.dependencies import get_container
from praxis_orchestrator.app.execution_records import (
    ExecutionAttemptStatus,
)
from praxis_orchestrator.infra.container import AppContainer
from praxis_orchestrator.infra.errors import AttemptNotFoundError
from praxis_orchestrator.infra.runtime.process_supervisor import attempt_log_path, follow_attempt_log


from praxis_orchestrator.api.routers.plans.schemas import (
    AttemptLogEntryResponse,
    AttemptLogResponse,
    AttemptTimelineResponse,
    ExecutionAttemptResponse,
    ExecutionRunTimelineResponse,
    PlanningArtifactResponse,
    PlanningOperationResponse,
    TaskExecutionTimelineResponse,
)

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("/{plan_id}/planning-artifacts", response_model=list[PlanningArtifactResponse])
def list_planning_artifacts(
    plan_id: str,
    purpose: str = "goal_contract",
    goal_id: str | None = None,
    # Bounded like `tail_lines` below. An unbounded `limit` reaches `LIMIT
    # :limit` verbatim, and SQLite reads a NEGATIVE limit as "no limit" — so
    # `?limit=-1` returned the plan's entire history (Phase 10A).
    limit: int = Query(default=20, ge=1, le=200),
    container: AppContainer = Depends(get_container),
) -> list[PlanningArtifactResponse]:
    """What earlier attempts at this artifact established, newest first.

    The payload itself is deliberately NOT served — it is model working state,
    can be large, and an operator needs to know an attempt happened and why it
    was refused, not to re-read the draft.

    **Omitting `goal_id` means EVERY goal, not "the plan-wide ones".** It used to
    mean the latter, because the store matches `goal_id IS :goal_id` and NULL
    therefore selects only plan-wide rows — so asking for a goal-scoped purpose
    without naming a goal returned `[]` while rows existed, and the honest
    reading of that empty list was "this was never recorded". Found in Phase 10B
    against `verification_baseline`, which is written per goal and looked
    entirely absent. Naming a `goal_id` still scopes to that goal.
    """
    artifacts = (
        container.planning_artifacts.latest(plan_id, purpose, goal_id=goal_id, limit=limit)
        if goal_id is not None
        else container.planning_artifacts.latest_across_goals(plan_id, purpose, limit=limit)
    )
    return [
        PlanningArtifactResponse(
            goal_id=item.goal_id,
            purpose=item.purpose,
            sequence=item.sequence,
            outcome=item.outcome,
            input_fingerprint=item.input_fingerprint,
            rejection_reasons=list(item.rejection_reasons),
            turns_used=item.turns_used,
            has_payload=item.payload is not None,
            created_at=item.created_at.isoformat(),
        )
        for item in artifacts
    ]


@router.delete("/{plan_id}/planning-artifacts", status_code=204)
def clear_planning_artifacts(
    plan_id: str,
    purpose: str = "goal_contract",
    goal_id: str | None = None,
    container: AppContainer = Depends(get_container),
) -> None:
    """Drop a goal's planning memory — the escape hatch for when the replay
    heuristics are wrong and a retry keeps being steered by a bad draft."""
    container.planning_artifacts.clear(plan_id, purpose, goal_id=goal_id)


@router.get("/{plan_id}/attempts", response_model=AttemptTimelineResponse)
def attempt_timeline(
    plan_id: str,
    container: AppContainer = Depends(get_container),
) -> AttemptTimelineResponse:
    """Durable task -> run -> attempt history, hydrated before live SSE."""
    with container.new_unit_of_work() as uow:
        uow.plans.get(plan_id)
        runs = uow.executions.list_runs(plan_id)
        attempts = uow.executions.list_attempts(plan_id)
        operations = uow.executions.list_planning_operations(plan_id)

    attempts_by_run: dict[str, list] = {}
    for attempt in attempts:
        attempts_by_run.setdefault(attempt.run_id, []).append(attempt)
    runs_by_task: dict[tuple[str, str], list] = {}
    for run in runs:
        runs_by_task.setdefault((run.goal_id, run.task_id), []).append(run)

    return AttemptTimelineResponse(
        planning_operations=[
            PlanningOperationResponse(
                id=item.id,
                purpose=item.purpose,
                target_goal_id=item.target_goal_id,
                status=item.status.value,
                created_at=item.created_at.isoformat(),
                updated_at=item.updated_at.isoformat(),
                started_at=item.started_at.isoformat() if item.started_at else None,
                completed_at=item.completed_at.isoformat() if item.completed_at else None,
                last_liveness_at=(
                    item.last_liveness_at.isoformat() if item.last_liveness_at else None
                ),
                model_request_count=item.model_request_count,
                tool_turn_count=item.tool_turn_count,
                runtime=item.runtime,
                provider_id=item.provider_id,
                model_id=item.model_id,
                failure_kind=item.failure_kind,
                retry_at=item.retry_at.isoformat() if item.retry_at else None,
                safe_message=item.safe_message,
            )
            for item in operations
        ],
        tasks=[
            TaskExecutionTimelineResponse(
                goal_id=goal_id,
                task_id=task_id,
                runs=[
                    ExecutionRunTimelineResponse(
                        id=run.id,
                        goal_id=run.goal_id,
                        task_id=run.task_id,
                        status=run.status.value,
                        started_at=run.started_at.isoformat(),
                        completed_at=(run.completed_at.isoformat() if run.completed_at else None),
                        attempts=[
                            ExecutionAttemptResponse(
                                id=attempt.id,
                                number=attempt.number,
                                task_attempt=attempt.task_attempt,
                                status=attempt.status.value,
                                started_at=attempt.started_at.isoformat(),
                                completed_at=(
                                    attempt.completed_at.isoformat()
                                    if attempt.completed_at
                                    else None
                                ),
                                last_liveness_at=(
                                    attempt.last_liveness_at.isoformat()
                                    if attempt.last_liveness_at
                                    else None
                                ),
                                timeout_seconds=attempt.timeout_seconds,
                                runtime=attempt.runtime,
                                provider_id=attempt.provider_id,
                                model_id=attempt.model_id,
                                failure_kind=attempt.failure_kind,
                                provider_code=attempt.provider_code,
                                retryable=attempt.retryable,
                                retry_at=(
                                    attempt.retry_at.isoformat() if attempt.retry_at else None
                                ),
                                limit_scope=(
                                    attempt.limit_scope.value if attempt.limit_scope else None
                                ),
                                exit_code=attempt.exit_code,
                                safe_message=attempt.safe_message,
                                stdout_tail=attempt.stdout_tail,
                                stderr_tail=attempt.stderr_tail,
                            )
                            for attempt in attempts_by_run.get(run.id, [])
                        ],
                    )
                    for run in task_runs
                ],
            )
            for (goal_id, task_id), task_runs in runs_by_task.items()
        ],
    )




@router.get("/{plan_id}/attempts/{attempt_id}/log", response_model=AttemptLogResponse)
def attempt_log(
    plan_id: str,
    attempt_id: str,
    tail_lines: int = Query(default=200, ge=0, le=2000),
    container: AppContainer = Depends(get_container),
) -> AttemptLogResponse:
    with container.new_unit_of_work() as uow:
        try:
            attempt = uow.executions.get_attempt(attempt_id)
        except KeyError as exc:
            raise AttemptNotFoundError(attempt_id) from exc
        if attempt.plan_id != plan_id:
            raise AttemptNotFoundError(attempt_id)

    path = attempt_log_path(container.orchestrator_home, attempt_id)
    if not path.exists():
        return AttemptLogResponse(entries=[], truncated=False)

    entries: list[AttemptLogEntryResponse] = []
    truncated = False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return AttemptLogResponse(entries=[], truncated=False)
    for line in lines:
        try:
            record = json.loads(line)
            if record.get("truncated") is True:
                truncated = True
                continue
            entries.append(AttemptLogEntryResponse.model_validate(record))
        except (ValueError, TypeError, AttributeError):
            continue
    return AttemptLogResponse(entries=entries[-tail_lines:] if tail_lines else [], truncated=truncated)


_TERMINAL_ATTEMPT_STATUSES = frozenset(
    {
        ExecutionAttemptStatus.SUCCEEDED,
        ExecutionAttemptStatus.FAILED,
        ExecutionAttemptStatus.ABANDONED,
    }
)


@router.get("/{plan_id}/attempts/{attempt_id}/log/stream")
async def attempt_log_stream(
    plan_id: str,
    attempt_id: str,
    request: Request,
    offset: int = Query(default=0, ge=0),
    container: AppContainer = Depends(get_container),
) -> StreamingResponse:
    """Live SSE tail of one attempt's RAW runtime stdout/stderr.

    Distinct from `/api/events` (telemetry): this streams the exact bytes the
    agent CLI wrote, straight from the bounded per-attempt runtime log, as they
    land. Each line is an SSE frame
    `id: <offset>` + `data: {monotonic_seconds,stream,text}`; an `event:
    truncated` frame means the bounded log rotated (reset your view); `event:
    end` closes the stream once the attempt reaches a terminal state. Resume
    without replay via the standard `Last-Event-ID` header (or `?offset=`).
    """
    with container.new_unit_of_work() as uow:
        try:
            attempt = uow.executions.get_attempt(attempt_id)
        except KeyError as exc:
            raise AttemptNotFoundError(attempt_id) from exc
        if attempt.plan_id != plan_id:
            raise AttemptNotFoundError(attempt_id)

    start = offset
    resume_id = request.headers.get("last-event-id")
    if resume_id and resume_id.isdigit():
        start = int(resume_id)

    path = attempt_log_path(container.orchestrator_home, attempt_id)

    def _is_terminal() -> bool:
        with container.new_unit_of_work() as uow:
            try:
                current = uow.executions.get_attempt(attempt_id)
            except KeyError:
                return True  # attempt vanished — nothing more will be written
            return current.status in _TERMINAL_ATTEMPT_STATUSES

    async def gen() -> AsyncIterator[str]:
        async for event in follow_attempt_log(
            path,
            is_terminal=_is_terminal,
            should_stop=request.is_disconnected,
            start_offset=start,
        ):
            if event.kind == "keepalive":
                yield ": keepalive\n\n"
            elif event.kind == "truncated":
                yield "event: truncated\ndata: {}\n\n"
            else:
                yield f"id: {event.offset}\ndata: {json.dumps(event.record)}\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class AgentEventResponse(BaseModel):
    id: int
    event_id: str
    plan_id: str
    task_id: str | None
    attempt: int
    seq: int
    type: str
    payload: dict[str, Any]
    occurred_at: str


@router.get("/{plan_id}/agent-events", response_model=list[AgentEventResponse])
def agent_events(
    plan_id: str,
    task_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),  # see list_planning_artifacts
    before_id: int | None = None,
    container: AppContainer = Depends(get_container),
) -> list[AgentEventResponse]:
    """The plan's fine-grained agent/reasoner telemetry history (most-recent
    first), optionally filtered to one task. 404s for an unknown plan."""
    import json

    uow = container.new_unit_of_work()
    with uow:
        uow.plans.get(plan_id)  # existence check -> PLAN_NOT_FOUND -> 404
    rows = container.agent_event_reader.list(
        plan_id, task_id=task_id, limit=limit, before_id=before_id
    )
    return [
        AgentEventResponse(
            id=r["id"],
            event_id=r["event_id"],
            plan_id=r["plan_id"],
            task_id=r["task_id"],
            attempt=r["attempt"],
            seq=r["seq"],
            type=r["type"],
            payload=json.loads(r["payload"]) if r["payload"] else {},
            occurred_at=r["occurred_at"],
        )
        for r in rows
    ]


