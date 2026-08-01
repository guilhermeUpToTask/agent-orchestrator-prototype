"""The previous rejection reaches the next attempt's prompt.

Live Tier 1 run: attempt 1 hit a provider rate limit, attempt 2 failed
`test author produced no executable checks`, and the goal blocked. Nothing about
either reached the agent, so a retry would have re-run an identical prompt
against an identical contract on a clean worktree.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from agent_orchestrator.app.execution_records import (
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionRun,
    ExecutionRunStatus,
)
from agent_orchestrator.app.runtime_failures import RuntimeFailure
from agent_orchestrator.domain.value_objects.lifecycle import FailureKind
from agent_orchestrator.infra.db.attempt_feedback_repository import SqliteAttemptFeedbackRepository

pytestmark = pytest.mark.integration

NOW = datetime(2026, 7, 26, tzinfo=timezone.utc)
PLAN, GOAL, TASK = "plan-1", "goal-1", "task-1"


def _record(container, number: int, *, kind: FailureKind, message: str) -> None:
    run_id, attempt_id = str(uuid4()), str(uuid4())
    with container.new_unit_of_work() as uow:
        uow.executions.add_run(
            ExecutionRun(
                id=run_id,
                plan_id=PLAN,
                goal_id=GOAL,
                task_id=TASK,
                status=ExecutionRunStatus.RUNNING,
                started_at=NOW + timedelta(minutes=number),
            )
        )
        uow.executions.add_attempt(
            ExecutionAttempt(
                id=attempt_id,
                run_id=run_id,
                plan_id=PLAN,
                goal_id=GOAL,
                task_id=TASK,
                number=number,
                task_attempt=number,
                status=ExecutionAttemptStatus.RUNNING,
                started_at=NOW + timedelta(minutes=number),
            )
        )
        uow.executions.finalize_attempt(
            attempt_id,
            attempt_status=ExecutionAttemptStatus.FAILED,
            run_status=ExecutionRunStatus.FAILED,
            completed_at=NOW + timedelta(minutes=number, seconds=30),
            failure=RuntimeFailure(kind=kind, safe_message=message, retryable=True),
        )


@pytest.fixture
def container(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
    from agent_orchestrator.infra.container import AppContainer
    from agent_orchestrator.infra.db.tables import Base

    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    # the project + plan rows the attempts hang off
    from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan
    from agent_orchestrator.domain.entities.project_definition import ProjectDefinition

    container.project_repo.add(ProjectDefinition(id="p", name="p", repo_url=None))
    with container.new_unit_of_work() as uow:
        uow.plans.save(Plan(id=PLAN, project_id="p", brief="b"))
    return container


def test_a_candidate_rejection_is_read_back_for_the_next_attempt(container) -> None:
    _record(
        container,
        1,
        kind=FailureKind.VERIFICATION_ERROR,
        message="path outside allowed scope: src/other.py; forbidden path changed: secrets/k",
    )
    reader = SqliteAttemptFeedbackRepository(container.session_factory)

    rejection = reader.last_rejection(PLAN, GOAL, TASK, task_revision=1)

    assert rejection is not None
    assert rejection.attempt_number == 1
    assert rejection.reasons == (
        "path outside allowed scope: src/other.py",
        "forbidden path changed: secrets/k",
    )


def test_a_capacity_failure_is_skipped_for_the_rejection_behind_it(container) -> None:
    """Exactly the live sequence. The newest failure was a provider rate limit,
    which says nothing an agent could act on; the candidate rejection before it
    still does."""
    _record(
        container,
        1,
        kind=FailureKind.VERIFICATION_ERROR,
        message="test author produced no executable checks",
    )
    _record(container, 2, kind=FailureKind.RATE_LIMIT, message="Upstream error: ResourceExhausted")
    reader = SqliteAttemptFeedbackRepository(container.session_factory)

    rejection = reader.last_rejection(PLAN, GOAL, TASK, task_revision=1)

    assert rejection is not None
    assert rejection.attempt_number == 1
    assert rejection.reasons == ("test author produced no executable checks",)


def test_an_orchestration_race_is_never_shown_to_an_agent(container) -> None:
    """A superseded cycle is not the agent's work. Showing it invites the agent
    to 'fix' something it has no business touching."""
    _record(
        container,
        1,
        kind=FailureKind.VERIFICATION_ERROR,
        message="goal promotion targets a superseded cycle",
    )
    reader = SqliteAttemptFeedbackRepository(container.session_factory)

    assert reader.last_rejection(PLAN, GOAL, TASK, task_revision=1) is None


def test_no_history_means_no_feedback(container) -> None:
    reader = SqliteAttemptFeedbackRepository(container.session_factory)

    assert reader.last_rejection(PLAN, GOAL, TASK, task_revision=1) is None
