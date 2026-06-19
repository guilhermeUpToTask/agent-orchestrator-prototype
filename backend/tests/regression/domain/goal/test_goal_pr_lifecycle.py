"""Regression tests for PR lifecycle state transitions."""
from __future__ import annotations

from src.domain.aggregates.goal import GoalAggregate, GoalStatus, TaskSummary
from src.domain.value_objects.status import TaskStatus


def _task(task_id: str = "t1", status: TaskStatus = TaskStatus.CREATED) -> TaskSummary:
    return TaskSummary(
        task_id=task_id,
        title=f"Task {task_id}",
        status=status,
        branch=f"goal/feat/task/{task_id}",
    )


def _pending_goal(name: str = "feat", tasks: list[TaskSummary] | None = None) -> GoalAggregate:
    return GoalAggregate.create(
        name=name,
        description="My feature goal",
        task_summaries=tasks or [_task("t1")],
    )


def _ready_for_review_goal() -> GoalAggregate:
    goal = _pending_goal()
    goal.start()
    goal.record_task_merged("t1")
    return goal


def _awaiting_pr_goal(pr_number: int = 42) -> GoalAggregate:
    goal = _ready_for_review_goal()
    goal.open_pr(pr_number, f"https://github.com/x/y/pull/{pr_number}", "sha-0001")
    return goal


class TestRegression:
    def test_new_commit_resets_approved_to_awaiting(self):
        goal = _awaiting_pr_goal()
        goal.sync_pr_state(
            pr_status="open", checks_passed=True, approved=True,
            head_sha="sha-0001", approval_count=1,
        )
        goal.advance_from_pr_state()
        assert goal.status == GoalStatus.APPROVED

        goal.sync_pr_state(
            pr_status="open", checks_passed=False, approved=False,
            head_sha="sha-0002", approval_count=0,
        )
        assert goal.status == GoalStatus.AWAITING_PR_APPROVAL
        assert not goal.pr_checks_passed
        assert not goal.pr_approved
        assert goal.pr_head_sha == "sha-0002"

    def test_same_sha_does_not_trigger_regression(self):
        goal = _awaiting_pr_goal()
        goal.sync_pr_state(
            pr_status="open", checks_passed=True, approved=True,
            head_sha="sha-0001", approval_count=1,
        )
        goal.advance_from_pr_state()
        assert goal.status == GoalStatus.APPROVED

        goal.sync_pr_state(
            pr_status="open", checks_passed=True, approved=True,
            head_sha="sha-0001", approval_count=1,
        )
        assert goal.status == GoalStatus.APPROVED

    def test_regression_history_entry_recorded(self):
        goal = _awaiting_pr_goal()
        goal.sync_pr_state(pr_status="open", checks_passed=True, approved=True,
                           head_sha="sha-0001", approval_count=1)
        goal.advance_from_pr_state()
        goal.sync_pr_state(pr_status="open", checks_passed=False, approved=False,
                           head_sha="sha-0002", approval_count=0)
        events = [h.event for h in goal.history]
        assert "goal.pr_regression" in events
