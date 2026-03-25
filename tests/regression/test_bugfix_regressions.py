"""
tests/regression/test_bugfix_regressions.py

Regression tests for historical bug fixes and compatibility behavior.
"""
import pytest
from unittest.mock import MagicMock, patch, ANY

from src.domain.aggregates.goal import GoalAggregate, GoalStatus, TaskSummary
from src.domain.value_objects.status import TaskStatus
from src.app.usecases.task_execute import TaskExecuteUseCase
from src.app.orchestrator import TaskGraphOrchestrator
from src.app.usecases.run_planning_session import RunPlanningSessionUseCase


# ---------------------------------------------------------------------------
# Bug 1: advance_from_pr_state check order
# ---------------------------------------------------------------------------

def test_advance_from_pr_state_checks_merged_before_closed():
    # Setup: Goal in AWAITING_PR_APPROVAL
    task = TaskSummary(task_id="t1", title="T1", status=TaskStatus.MERGED, branch="b1")
    goal = GoalAggregate.create(name="feat", description="desc", task_summaries=[task])
    
    # Manually set to AWAITING_PR_APPROVAL via open_pr logic simulation
    goal.status = GoalStatus.AWAITING_PR_APPROVAL
    goal.pr_number = 42
    
    # Case: pr_status is "merged". If we check "closed" first and it's some odd state,
    # it might fail. Here we verify "merged" is caught.
    # Actually, the fix was to ensure "merged" is checked before "closed" because
    # they were mutually exclusive in the code's if/elif (previously if/if).
    
    goal.pr_status = "merged"
    goal.advance_from_pr_state()
    
    assert goal.status == GoalStatus.MERGED


# ---------------------------------------------------------------------------
# Bug 3: Double task load in TaskExecuteUseCase
# ---------------------------------------------------------------------------

def test_task_execute_prepare_workspace_uses_passed_constraints():
    # Mock dependencies
    task_repo = MagicMock()
    git_ws = MagicMock()
    
    # Setup task with specific constraints
    task_id = "task-123"
    task = MagicMock()
    task.task_id = task_id
    task.execution.constraints = {"task_branch": "custom-branch", "goal_branch": "feature-base"}
    
    # We want to verify that _prepare_workspace uses the passed constraints
    # and doesn't call task_repo.load() an extra time.
    
    usecase = TaskExecuteUseCase(
        repo_url="http://git",
        task_repo=task_repo,
        agent_registry=MagicMock(),
        event_port=MagicMock(),
        lease_port=MagicMock(),
        git_workspace=git_ws,
        runtime_factory=MagicMock(),
        logs_port=MagicMock(),
        test_runner=MagicMock(),
    )
    
    # We test the internal _prepare_workspace directly to verify the fix
    ws_path, branch = usecase._prepare_workspace(task_id, task.execution.constraints)
    
    assert branch == "custom-branch"
    git_ws.create_workspace.assert_called_once()
    # verify it used the base_branch from constraints
    git_ws.checkout_main_and_create_branch.assert_called_once_with(
        ANY, "custom-branch", base_branch="feature-base"
    )
    
    # Verify task_repo.load was NOT called by _prepare_workspace
    task_repo.load.assert_not_called()


# ---------------------------------------------------------------------------
# Bug 4: Handlers dict built twice
# ---------------------------------------------------------------------------

def test_orchestrator_dispatch_uses_canonical_handlers():
    orchestrator = TaskGraphOrchestrator(
        task_repo=MagicMock(),
        goal_repo=MagicMock(),
        event_port=MagicMock(),
        merge_usecase=MagicMock(),
        cancel_usecase=MagicMock(),
    )
    
    event = MagicMock()
    event.type = "task.completed"
    event.payload = {"task_id": "t1"}
    
    with patch.object(orchestrator, "_on_task_completed") as mock_handler:
        orchestrator._dispatch(event)
        mock_handler.assert_called_once_with(event)


# ---------------------------------------------------------------------------
# Issue 5: GoalStatus.COMPLETED normalization
# ---------------------------------------------------------------------------

def test_goal_aggregate_normalizes_completed_to_merged():
    raw_data = {
        "goal_id": "goal-1",
        "name": "legacy-goal",
        "description": "desc",
        "branch": "goal/legacy",
        "status": "completed", # Legacy string
        "tasks": {}
    }
    
    with pytest.warns(DeprecationWarning, match="GoalStatus.COMPLETED is deprecated"):
        goal = GoalAggregate.model_validate(raw_data)
        
    assert goal.status == GoalStatus.MERGED


# ---------------------------------------------------------------------------
# Issue 6: Deprecation warning for RunPlanningSessionUseCase
# ---------------------------------------------------------------------------

def test_run_planning_session_init_raises_deprecation_warning():
    with pytest.warns(DeprecationWarning, match="RunPlanningSessionUseCase is deprecated"):
        # We don't need real dependencies just to check the init warning
        # but we need enough to satisfy type hints if necessary.
        RunPlanningSessionUseCase(
            context_assembler=MagicMock(),
            planner_runtime=MagicMock(),
            session_repo=MagicMock(),
            goal_init=MagicMock(),
            validator=MagicMock(),
            project_state=MagicMock(),
            agent_registry=MagicMock(),
        )
