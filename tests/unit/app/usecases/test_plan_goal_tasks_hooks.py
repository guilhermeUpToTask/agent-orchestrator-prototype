"""
Unit tests for PlanGoalTasksUseCase progress_hook injection.
"""
from unittest.mock import MagicMock, patch
import pytest

from src.app.usecases.plan_goal_tasks import PlanGoalTasksUseCase
from src.domain.aggregates.goal import GoalAggregate
from src.domain.ports.planner import PlannerRuntimePort


def _make_usecase(progress_hook=None):
    task_creation = MagicMock()
    goal_repo = MagicMock()
    planner_runtime = MagicMock(spec=PlannerRuntimePort)
    event_port = MagicMock()

    usecase = PlanGoalTasksUseCase(
        task_creation=task_creation,
        goal_repo=goal_repo,
        planner_runtime=planner_runtime,
        event_port=event_port,
        progress_hook=progress_hook,
    )
    return usecase, task_creation, goal_repo, planner_runtime, event_port


def _make_goal(goal_id="goal-123", name="setup-auth"):
    goal = MagicMock(spec=GoalAggregate)
    goal.goal_id = goal_id
    goal.name = name
    goal.description = "Setup authentication"
    goal.tasks = []
    goal.branch = f"goal/{name}"
    return goal


class TestPlanGoalTasksProgressHook:

    def test_progress_hook_fires_jit_start_before_llm(self):
        fired = []

        def hook(event_type, data):
            fired.append((event_type, data))

        usecase, task_creation, goal_repo, planner_runtime, _ = _make_usecase(progress_hook=hook)
        goal = _make_goal()
        goal_repo.get.return_value = goal

        # LLM returns no tasks — execute will return early after jit_start
        planner_runtime.run_session.return_value = MagicMock()

        usecase.execute("goal-123")

        start_events = [(et, d) for et, d in fired if et == "jit_start"]
        assert len(start_events) == 1
        assert start_events[0][1]["goal_id"] == "goal-123"
        assert start_events[0][1]["goal_name"] == "setup-auth"

    def test_progress_hook_fires_jit_end_with_task_ids(self):
        from src.domain import GoalTaskDef

        fired = []

        def hook(event_type, data):
            fired.append((event_type, data))

        usecase, task_creation, goal_repo, planner_runtime, _ = _make_usecase(progress_hook=hook)
        goal = _make_goal()
        goal_repo.get.return_value = goal
        goal_repo.update_if_version.return_value = True

        # Simulate LLM submitting tasks via the tool
        task_def_1 = MagicMock()
        task_def_1.task_id = "write-tests"
        task_def_1.title = "Write failing tests"
        task_def_1.description = "Tests for auth"
        task_def_1.capability = "coding"
        task_def_1.files_allowed_to_modify = ["tests/*"]
        task_def_1.depends_on = []
        task_def_1.acceptance_criteria = ["Tests fail"]
        task_def_1.test_command = None
        task_def_1.constraints = {}
        task_def_1.max_retries = 3
        task_def_1.min_version = 1

        task_def_2 = MagicMock()
        task_def_2.task_id = "implement"
        task_def_2.title = "Implement"
        task_def_2.description = "Implementation"
        task_def_2.capability = "coding"
        task_def_2.files_allowed_to_modify = ["src/*"]
        task_def_2.depends_on = ["write-tests"]
        task_def_2.acceptance_criteria = ["All tests pass"]
        task_def_2.test_command = "pytest"
        task_def_2.constraints = {}
        task_def_2.max_retries = 3
        task_def_2.min_version = 1

        # Patch _invoke_llm to return tasks directly
        usecase._invoke_llm = MagicMock(return_value=[task_def_1, task_def_2])

        usecase.execute("goal-123")

        end_events = [(et, d) for et, d in fired if et == "jit_end"]
        assert len(end_events) == 1
        assert set(end_events[0][1]["task_ids"]) == {"write-tests", "implement"}

    def test_progress_hook_none_does_not_raise(self):
        usecase, task_creation, goal_repo, planner_runtime, _ = _make_usecase(progress_hook=None)
        goal = _make_goal()
        goal_repo.get.return_value = goal
        planner_runtime.run_session.return_value = MagicMock()

        # Should not raise even with no hook
        usecase.execute("goal-123")

    def test_jit_start_fires_before_llm_is_called(self):
        """Verify ordering: jit_start fires before the LLM session."""
        order = []

        def hook(event_type, data):
            order.append(f"hook:{event_type}")

        usecase, task_creation, goal_repo, planner_runtime, _ = _make_usecase(progress_hook=hook)
        goal = _make_goal()
        goal_repo.get.return_value = goal

        original_run = planner_runtime.run_session.side_effect

        def run_session_spy(*args, **kwargs):
            order.append("llm_called")
            return MagicMock()

        planner_runtime.run_session.side_effect = run_session_spy

        usecase.execute("goal-123")

        assert order.index("hook:jit_start") < order.index("llm_called")
