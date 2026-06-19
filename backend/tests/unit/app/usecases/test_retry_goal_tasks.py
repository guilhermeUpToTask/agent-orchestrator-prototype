"""
tests/unit/app/usecases/test_retry_goal_tasks.py — bulk retry of FAILED tasks.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.app.usecases.retry_goal_tasks import RetryGoalTasksUseCase
from src.app.usecases.task_retry import TaskRetryUseCase
from src.domain import AgentSelector, ExecutionSpec, TaskAggregate, TaskStatus


def _task(task_id: str, status: TaskStatus) -> TaskAggregate:
    return TaskAggregate(
        task_id=task_id,
        feature_id="g-1",
        title="T", description="D",
        agent_selector=AgentSelector(required_capability="code:backend"),
        execution=ExecutionSpec(type="code:backend"),
        status=status,
    )


def _goal(goal_id: str, task_ids: list[str]):
    g = MagicMock()
    g.goal_id = goal_id
    g.tasks = {tid: MagicMock() for tid in task_ids}
    return g


def _make_uc(goal, tasks_by_id):
    goal_repo = MagicMock()
    goal_repo.get.return_value = goal
    goal_repo.list_all.return_value = [goal]
    task_repo = MagicMock()
    task_repo.load.side_effect = lambda tid: tasks_by_id[tid]
    events = MagicMock()
    retry = TaskRetryUseCase(task_repo=task_repo, event_port=events)
    uc = RetryGoalTasksUseCase(goal_repo=goal_repo, task_repo=task_repo, task_retry=retry)
    return uc, task_repo, events


def test_retry_goal_requeues_only_failed():
    tasks = {
        "a": _task("a", TaskStatus.FAILED),
        "b": _task("b", TaskStatus.SUCCEEDED),
        "c": _task("c", TaskStatus.FAILED),
    }
    goal = _goal("g-1", ["a", "b", "c"])
    uc, task_repo, events = _make_uc(goal, tasks)

    result = uc.retry_goal("g-1")

    assert sorted(result.requeued) == ["a", "c"]
    assert result.goals_touched == ["g-1"]
    assert tasks["a"].status == TaskStatus.REQUEUED
    assert tasks["b"].status == TaskStatus.SUCCEEDED  # untouched
    assert events.publish.call_count == 2


def test_retry_goal_clears_unassignable_reason():
    t = _task("a", TaskStatus.FAILED)
    t.unassignable_reason = "No active agent with capability 'code:backend'"
    goal = _goal("g-1", ["a"])
    uc, _, _ = _make_uc(goal, {"a": t})

    uc.retry_goal("g-1")

    assert t.unassignable_reason is None


def test_retry_goal_missing_goal_raises():
    uc, _, _ = _make_uc(_goal("g-1", []), {})
    uc._goal_repo.get.return_value = None
    with pytest.raises(KeyError):
        uc.retry_goal("nope")


def test_retry_reopens_failed_goal_and_requeues_canceled():
    from src.domain import GoalAggregate, GoalStatus, TaskStatus as TS, TaskSummary

    # A canceled task fails its goal (record_task_canceled).
    goal = GoalAggregate.create(
        name="g",
        description="d",
        task_summaries=[
            TaskSummary(task_id="a", title="A", status=TS.IN_PROGRESS, branch="task/g/a"),
        ],
        goal_id="g-1",
    )
    goal.start()
    goal.record_task_canceled("a", reason="git boom")
    assert goal.status == GoalStatus.FAILED

    task = _task("a", TS.CANCELED)
    goal_repo = MagicMock()
    goal_repo.get.return_value = goal
    task_repo = MagicMock()
    task_repo.load.return_value = task
    retry = TaskRetryUseCase(task_repo=task_repo, event_port=MagicMock())
    uc = RetryGoalTasksUseCase(goal_repo=goal_repo, task_repo=task_repo, task_retry=retry)

    result = uc.retry_goal("g-1")

    assert result.requeued == ["a"]
    assert goal.status == GoalStatus.RUNNING          # reopened
    assert goal.failure_reason is None
    assert goal.tasks["a"].status == TS.REQUEUED       # summary synced
    assert task.status == TS.REQUEUED                  # real task requeued
    goal_repo.save.assert_called_once()


def test_retry_all_spans_goals():
    tasks = {"a": _task("a", TaskStatus.FAILED)}
    goal = _goal("g-1", ["a"])
    uc, _, _ = _make_uc(goal, tasks)

    result = uc.retry_all()

    assert result.requeued == ["a"]
    assert result.goals_touched == ["g-1"]
