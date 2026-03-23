"""
Unit tests for phase completion trigger in AdvanceGoalFromPRUseCase.
"""
from unittest.mock import MagicMock, Mock

import pytest

from src.app.usecases.advance_goal_from_pr import AdvanceGoalFromPRUseCase
from src.domain.aggregates.goal import GoalStatus, GoalAggregate, TaskSummary
from src.domain.aggregates.project_plan import (
    Phase,
    PhaseStatus,
    ProjectPlan,
    ProjectPlanStatus,
)
from src.domain.value_objects.status import TaskStatus


class TestPhaseCompletionTrigger:
    """Test that phase completion triggers PHASE_REVIEW when all goals are MERGED."""

    def setup_method(self):
        self.goal_repo = MagicMock()
        self.event_port = MagicMock()
        self.unblock_goals_usecase = MagicMock()
        self.plan_repo = MagicMock()

        self.usecase = AdvanceGoalFromPRUseCase(
            goal_repo=self.goal_repo,
            event_port=self.event_port,
            unblock_goals_usecase=self.unblock_goals_usecase,
            plan_repo=self.plan_repo,
        )

    def test_triggers_review_when_all_phase_goals_merged(self):
        """When all goals in the active phase reach MERGED, trigger PHASE_REVIEW."""
        # Create a plan in PHASE_ACTIVE status
        plan = ProjectPlan.create("Test vision")
        brief = plan.brief or Mock(
            vision="Test",
            constraints=[],
            phase_1_exit_criteria="",
            open_questions=[],
        )
        plan = plan.approve_brief(brief)
        phases = [
            Phase(
                index=0,
                name="Foundation",
                goal="Auth system working",
                goal_names=["goal1", "goal2"],
                status=PhaseStatus.PLANNED,
                lessons="",
                exit_criteria="",
            )
        ]
        plan = plan.approve_phase(phases)
        self.plan_repo.get.return_value = plan

        # Create two goals in MERGED status
        goal1 = GoalAggregate.create(
            name="goal1",
            description="Goal 1",
            task_summaries=[
                TaskSummary(
                    task_id="task1",
                    title="Task 1",
                    status=TaskStatus.MERGED,
                    branch="task/1",
                )
            ],
        )
        goal1.status = GoalStatus.MERGED

        goal2 = GoalAggregate.create(
            name="goal2",
            description="Goal 2",
            task_summaries=[
                TaskSummary(
                    task_id="task2",
                    title="Task 2",
                    status=TaskStatus.MERGED,
                    branch="task/2",
                )
            ],
        )
        goal2.status = GoalStatus.MERGED

        self.goal_repo.list_all.return_value = [goal1, goal2]

        # Execute the use case (simulating a goal reaching MERGED)
        self.usecase._check_phase_completion()

        # Verify that plan.trigger_review() was called by checking plan_repo.save
        # (trigger_review returns a new plan instance)
        self.plan_repo.save.assert_called()
        saved_plan = self.plan_repo.save.call_args[0][0]
        assert saved_plan.status == ProjectPlanStatus.PHASE_REVIEW

    def test_does_not_trigger_when_some_goals_not_merged(self):
        """When not all goals in the active phase are MERGED, don't trigger."""
        plan = ProjectPlan.create("Test vision")
        brief = plan.brief or Mock(
            vision="Test",
            constraints=[],
            phase_1_exit_criteria="",
            open_questions=[],
        )
        plan = plan.approve_brief(brief)
        phases = [
            Phase(
                index=0,
                name="Foundation",
                goal="Auth system working",
                goal_names=["goal1", "goal2"],
                status=PhaseStatus.PLANNED,
                lessons="",
                exit_criteria="",
            )
        ]
        plan = plan.approve_phase(phases)
        self.plan_repo.get.return_value = plan

        # Create one goal in MERGED status, one in RUNNING
        goal1 = GoalAggregate.create(
            name="goal1",
            description="Goal 1",
            task_summaries=[
                TaskSummary(
                    task_id="task1",
                    title="Task 1",
                    status=TaskStatus.MERGED,
                    branch="task/1",
                )
            ],
        )
        goal1.status = GoalStatus.MERGED

        goal2 = GoalAggregate.create(
            name="goal2",
            description="Goal 2",
            task_summaries=[
                TaskSummary(
                    task_id="task2",
                    title="Task 2",
                    status=TaskStatus.ASSIGNED,
                    branch="task/2",
                )
            ],
        )
        goal2.status = GoalStatus.RUNNING

        self.goal_repo.list_all.return_value = [goal1, goal2]

        # Execute the use case
        self.usecase._check_phase_completion()

        # Verify that plan was NOT saved (no status change)
        self.plan_repo.save.assert_not_called()

    def test_does_not_trigger_on_empty_phase(self):
        """When phase has no goals, don't trigger review."""
        plan = ProjectPlan.create("Test vision")
        brief = plan.brief or Mock(
            vision="Test",
            constraints=[],
            phase_1_exit_criteria="",
            open_questions=[],
        )
        plan = plan.approve_brief(brief)
        phases = [
            Phase(
                index=0,
                name="Foundation",
                goal="Auth system working",
                goal_names=[],  # Empty phase
                status=PhaseStatus.PLANNED,
                lessons="",
                exit_criteria="",
            )
        ]
        plan = plan.approve_phase(phases)
        self.plan_repo.get.return_value = plan

        self.goal_repo.list_all.return_value = []

        # Execute the use case
        self.usecase._check_phase_completion()

        # Verify that plan was NOT saved
        self.plan_repo.save.assert_not_called()

    def test_does_not_trigger_when_not_in_phase_active(self):
        """When plan is not in PHASE_ACTIVE status, don't trigger."""
        plan = ProjectPlan.create("Test vision")
        self.plan_repo.get.return_value = plan

        # Execute the use case
        self.usecase._check_phase_completion()

        # Verify that plan was NOT saved
        self.plan_repo.save.assert_not_called()

    def test_does_nothing_when_plan_repo_is_none(self):
        """When plan_repo is None (backward compatibility), do nothing."""
        usecase = AdvanceGoalFromPRUseCase(
            goal_repo=self.goal_repo,
            event_port=self.event_port,
            unblock_goals_usecase=self.unblock_goals_usecase,
            plan_repo=None,  # None for backward compat
        )

        # Should not raise an error
        usecase._check_phase_completion()

        # Verify that nothing was called
        self.goal_repo.list_all.assert_not_called()

    def test_does_nothing_when_no_plan_exists(self):
        """When plan doesn't exist, do nothing."""
        self.plan_repo.get.return_value = None

        # Execute the use case
        self.usecase._check_phase_completion()

        # Verify that nothing was called
        self.goal_repo.list_all.assert_not_called()

    def test_does_nothing_when_no_active_phase(self):
        """When there's no active phase, do nothing."""
        plan = ProjectPlan.create("Test vision")
        plan = plan.model_copy(update={"current_phase_index": -1})
        self.plan_repo.get.return_value = plan

        # Execute the use case
        self.usecase._check_phase_completion()

        # Verify that nothing was called
        self.goal_repo.list_all.assert_not_called()
