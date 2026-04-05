"""
tests/unit/app/usecases/test_jit_planner.py

Unit tests for the Two-Tiered JIT Planning refactor:
  - GoalSpec now allows empty tasks
  - GoalAggregate.append_task_summary
  - PlanGoalTasksUseCase (happy path + edge cases)
  - TaskGraphOrchestrator routes goal.unblocked → JIT planner
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.domain import DomainEvent, GoalTaskDef, TaskStatus, TaskSummary
from src.domain.aggregates.goal import GoalAggregate, GoalStatus
from src.domain.value_objects.goal import GoalSpec
from src.app.usecases.plan_goal_tasks import PlanGoalTasksUseCase
from src.domain.ports.planner import PlannerOutput, PlannerTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_goal(name: str = "setup-db", tasks: dict | None = None) -> GoalAggregate:
    goal = GoalAggregate.create(
        name=name,
        description="Stand up the database layer",
        task_summaries=[],
    )
    if tasks:
        goal.tasks = tasks
    return goal


def _make_task_def(task_id: str, depends_on: list[str] | None = None) -> GoalTaskDef:
    return GoalTaskDef(
        task_id=task_id,
        title=f"Title {task_id}",
        description="desc",
        capability="coding",
        files_allowed_to_modify=["src/*"] if "impl" in task_id else ["tests/*"],
        depends_on=depends_on or [],
    )


# ---------------------------------------------------------------------------
# Step 1 — Domain: GoalSpec allows empty tasks
# ---------------------------------------------------------------------------

class TestGoalSpecOptionalTasks:

    def test_empty_tasks_creates_spec(self):
        spec = GoalSpec(name="my-goal", description="JIT goal", tasks=[])
        assert spec.tasks == []

    def test_omit_tasks_defaults_to_empty(self):
        spec = GoalSpec(name="my-goal", description="JIT goal")
        assert spec.tasks == []

    def test_non_empty_tasks_still_validated(self):
        with pytest.raises(Exception):
            # cycle: t1 depends on t2, t2 depends on t1
            GoalSpec(
                name="cycle-goal",
                description="bad",
                tasks=[
                    GoalTaskDef(task_id="t1", title="T1", description="d",
                                capability="coding", depends_on=["t2"]),
                    GoalTaskDef(task_id="t2", title="T2", description="d",
                                capability="coding", depends_on=["t1"]),
                ],
            )

    def test_goal_spec_unknown_dep_still_rejected(self):
        with pytest.raises(Exception):
            GoalSpec(
                name="bad-deps",
                description="d",
                tasks=[
                    GoalTaskDef(task_id="t1", title="T1", description="d",
                                capability="coding", depends_on=["nonexistent"]),
                ],
            )


# ---------------------------------------------------------------------------
# Step 1 — Domain: GoalAggregate.append_task_summary
# ---------------------------------------------------------------------------

class TestAppendTaskSummary:

    def test_append_adds_task_to_dict(self):
        goal = _make_goal()
        summary = TaskSummary(
            task_id="write-tests",
            title="Write failing tests",
            status=TaskStatus.CREATED,
            branch="goal/setup-db/task/write-tests",
        )
        goal.append_task_summary(summary)
        assert "write-tests" in goal.tasks
        assert goal.tasks["write-tests"].title == "Write failing tests"

    def test_append_bumps_state_version(self):
        goal = _make_goal()
        v0 = goal.state_version
        goal.append_task_summary(TaskSummary(
            task_id="t1", title="T", status=TaskStatus.CREATED,
            branch="goal/g/task/t1",
        ))
        assert goal.state_version == v0 + 1

    def test_append_records_history_event(self):
        goal = _make_goal()
        goal.append_task_summary(TaskSummary(
            task_id="t1", title="T", status=TaskStatus.CREATED,
            branch="goal/g/task/t1",
        ))
        events = [h.event for h in goal.history]
        assert "goal.task_added" in events

    def test_append_two_tasks(self):
        goal = _make_goal()
        for tid in ["write-tests", "implement"]:
            goal.append_task_summary(TaskSummary(
                task_id=tid, title=tid, status=TaskStatus.CREATED,
                branch=f"goal/setup-db/task/{tid}",
            ))
        assert len(goal.tasks) == 2

    def test_append_to_terminal_goal_raises(self):
        goal = _make_goal()
        goal.status = GoalStatus.FAILED
        goal.failure_reason = "forced"
        with pytest.raises(ValueError, match="already"):
            goal.append_task_summary(TaskSummary(
                task_id="t1", title="T", status=TaskStatus.CREATED,
                branch="g",
            ))


# ---------------------------------------------------------------------------
# Step 3 — PlanGoalTasksUseCase
# ---------------------------------------------------------------------------

def _make_use_case(goal: GoalAggregate, llm_task_defs: list[dict] | None = None):
    """Build a PlanGoalTasksUseCase wired with simple fakes."""

    goal_repo = MagicMock()
    goal_repo.get.return_value = goal
    goal_repo.update_if_version.return_value = True

    event_port = MagicMock()
    task_creation = MagicMock()

    # Fake planner runtime: calls the submit_tdd_tasks tool handler with the
    # provided task definitions on its first invocation.
    planner_runtime = MagicMock()

    if llm_task_defs is not None:
        def fake_run_session(prompt, tools, max_turns=3, session_callback=None):
            # Find the submit_tdd_tasks tool and call its handler.
            tool: PlannerTool = next(t for t in tools if t.name == "submit_tdd_tasks")
            tool.handler({"tasks_json": json.dumps(llm_task_defs)})
            return PlannerOutput(reasoning="ok", roadmap_raw={}, raw_text="")

        planner_runtime.run_session.side_effect = fake_run_session

    uc = PlanGoalTasksUseCase(
        task_creation=task_creation,
        goal_repo=goal_repo,
        planner_runtime=planner_runtime,
        event_port=event_port,
    )
    return uc, goal_repo, task_creation, event_port, planner_runtime


_VALID_TDD_TASKS = [
    {
        "task_id": "write-tests",
        "title": "Write failing tests",
        "description": "Create pytest tests for the DB layer",
        "capability": "coding",
        "files_allowed_to_modify": ["tests/*"],
        "depends_on": [],
        "acceptance_criteria": ["Tests fail before implementation"],
        "test_command": None,
    },
    {
        "task_id": "implement-db",
        "title": "Implement DB layer",
        "description": "Write code to pass the tests",
        "capability": "coding",
        "files_allowed_to_modify": ["src/*"],
        "depends_on": ["write-tests"],
        "acceptance_criteria": ["All tests pass"],
        "test_command": "pytest tests/",
    },
]


class TestPlanGoalTasksUseCase:

    def test_happy_path_creates_two_tasks(self):
        goal = _make_goal()
        uc, goal_repo, task_creation, _, _ = _make_use_case(goal, _VALID_TDD_TASKS)
        uc.execute(goal.goal_id)
        assert task_creation.create_task.call_count == 2

    def test_happy_path_appends_summaries_to_goal(self):
        goal = _make_goal()
        uc, goal_repo, _, _, _ = _make_use_case(goal, _VALID_TDD_TASKS)
        uc.execute(goal.goal_id)
        # The goal passed to update_if_version should have 2 tasks.
        call_args = goal_repo.update_if_version.call_args
        updated_goal: GoalAggregate = call_args[0][1]
        assert len(updated_goal.tasks) == 2

    def test_noop_when_goal_already_has_tasks(self):
        existing_summary = TaskSummary(
            task_id="existing", title="T", status=TaskStatus.CREATED,
            branch="goal/g/task/existing",
        )
        goal = _make_goal(tasks={"existing": existing_summary})
        uc, goal_repo, task_creation, _, planner_runtime = _make_use_case(goal)
        uc.execute(goal.goal_id)
        planner_runtime.run_session.assert_not_called()
        task_creation.create_task.assert_not_called()

    def test_noop_when_goal_not_found(self):
        goal = _make_goal()
        uc, goal_repo, task_creation, _, planner_runtime = _make_use_case(goal)
        goal_repo.get.return_value = None
        uc.execute("missing-id")
        planner_runtime.run_session.assert_not_called()
        task_creation.create_task.assert_not_called()

    def test_no_tasks_returned_by_llm_logs_and_returns(self):
        goal = _make_goal()
        # LLM returns an empty list (invalid) — handler rejects it.
        uc, goal_repo, task_creation, _, _ = _make_use_case(goal, [])
        uc.execute(goal.goal_id)
        # No tasks created, no goal save attempted.
        task_creation.create_task.assert_not_called()
        goal_repo.update_if_version.assert_not_called()

    def test_llm_runtime_error_is_handled_gracefully(self):
        from src.domain.ports.planner import PlannerRuntimeError
        goal = _make_goal()
        uc, goal_repo, task_creation, _, planner_runtime = _make_use_case(goal)
        planner_runtime.run_session.side_effect = PlannerRuntimeError("timeout")
        # Should not raise.
        uc.execute(goal.goal_id)
        task_creation.create_task.assert_not_called()

    def test_cas_retry_on_conflict(self):
        """update_if_version fails first attempt, succeeds on second."""
        goal = _make_goal()
        uc, goal_repo, _, _, _ = _make_use_case(goal, _VALID_TDD_TASKS)
        # First CAS call returns False (conflict), second returns True.
        goal_repo.update_if_version.side_effect = [False, True]
        uc.execute(goal.goal_id)
        assert goal_repo.update_if_version.call_count == 2


# ---------------------------------------------------------------------------
# Step 4 — Orchestrator routes goal.unblocked → JIT planner
# ---------------------------------------------------------------------------

class TestOrchestratorJitRouting:

    def _make_orchestrator(self, plan_goal_tasks_uc):
        from src.app.orchestrator import TaskGraphOrchestrator

        task_repo = MagicMock()
        goal_repo = MagicMock()
        event_port = MagicMock()
        event_port.subscribe_many.return_value = iter([])
        merge_uc = MagicMock()
        cancel_uc = MagicMock()

        return TaskGraphOrchestrator(
            task_repo=task_repo,
            goal_repo=goal_repo,
            event_port=event_port,
            merge_usecase=merge_uc,
            cancel_usecase=cancel_uc,
            plan_goal_tasks=plan_goal_tasks_uc,
        )

    def test_goal_unblocked_calls_jit_planner(self):
        plan_goal_tasks = MagicMock()
        orch = self._make_orchestrator(plan_goal_tasks)
        event = DomainEvent(
            type="goal.unblocked",
            producer="test",
            payload={"goal_id": "goal-abc123"},
        )
        orch._dispatch(event)
        plan_goal_tasks.execute.assert_called_once_with("goal-abc123")

    def test_goal_unblocked_no_jit_planner_does_not_raise(self):
        orch = self._make_orchestrator(plan_goal_tasks_uc=None)
        event = DomainEvent(
            type="goal.unblocked",
            producer="test",
            payload={"goal_id": "goal-abc123"},
        )
        # Should log a warning but not raise.
        orch._dispatch(event)

    def test_goal_unblocked_missing_goal_id_is_noop(self):
        plan_goal_tasks = MagicMock()
        orch = self._make_orchestrator(plan_goal_tasks)
        event = DomainEvent(
            type="goal.unblocked",
            producer="test",
            payload={},  # no goal_id
        )
        orch._dispatch(event)
        plan_goal_tasks.execute.assert_not_called()
