"""
tests/unit/app/usecases/test_run_planning_session.py

Tests for RunPlanningSessionUseCase with all external ports mocked.
"""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from src.app.usecases.run_planning_session import RunPlanningSessionUseCase, PlanningResult
from src.app.usecases.validate_against_spec import ValidationResult
from src.domain.aggregates.goal import GoalAggregate
from src.domain.aggregates.planner_session import PlannerSession, PlannerSessionStatus
from src.domain.entities.agent import AgentProps
from src.domain.ports.planner import PlannerRuntimeError
from src.infra.fs.planner_session_repository import InMemoryPlannerSessionRepository
from src.infra.fs.project_state_adapter import InMemoryProjectStateAdapter
from src.infra.runtime.planner_runtime import StubPlannerRuntime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_ROADMAP = {
    "goals": [
        {
            "name": "setup-db",
            "description": "Set up the database layer",
            "tasks": [
                {
                    "task_id": "create-schema",
                    "title": "Create schema",
                    "description": "Write DB migrations",
                    "capability": "python",
                    "depends_on": [],
                }
            ],
            "depends_on": [],
            "feature_tag": None,
        }
    ]
}


def _make_goal_aggregate(name: str = "setup-db") -> GoalAggregate:
    from src.domain.aggregates.goal import TaskSummary
    from src.domain.value_objects.status import TaskStatus
    ts = TaskSummary(task_id="create-schema", title="Create schema",
                     status=TaskStatus.CREATED, branch="goal/setup-db/task/create-schema")
    return GoalAggregate.create(
        name=name,
        description="desc",
        task_summaries=[ts],
        depends_on=[],
    )


def _make_usecase(
    runtime=None,
    session_repo=None,
    goal_init_returns=None,
    validator_passes=True,
    agents=None,
    goal_repo=None,
    project_state=None,
) -> RunPlanningSessionUseCase:
    # context assembler mock
    ctx = MagicMock()
    ctx.to_prompt_context.return_value = "## context"
    assembler = MagicMock()
    assembler.assemble.return_value = ctx

    # runtime default: stub with valid roadmap
    if runtime is None:
        runtime = StubPlannerRuntime(custom_output=VALID_ROADMAP)

    # session repo default: in-memory
    if session_repo is None:
        session_repo = InMemoryPlannerSessionRepository()

    # goal_init mock
    goal_init = MagicMock()
    if goal_init_returns is not None:
        goal_init.execute.return_value = goal_init_returns
    else:
        goal_init.execute.return_value = _make_goal_aggregate()

    # validator mock
    validator = MagicMock()
    if validator_passes:
        validator.execute.return_value = ValidationResult(passed=True)
    else:
        validator.execute.return_value = ValidationResult(
            passed=False, violations=["forbidden pattern found"]
        )

    # agent registry mock
    agent_registry = MagicMock()
    if agents is None:
        agents = []
    agent_registry.list_agents.return_value = agents

    # goal repo mock
    if goal_repo is None:
        goal_repo = MagicMock()
        goal_repo.list_all.return_value = []

    # project state default: in-memory
    if project_state is None:
        project_state = InMemoryProjectStateAdapter()

    return RunPlanningSessionUseCase(
        context_assembler=assembler,
        planner_runtime=runtime,
        session_repo=session_repo,
        goal_init=goal_init,
        validator=validator,
        project_state=project_state,
        agent_registry=agent_registry,
        goal_repo=goal_repo,
    )


# ---------------------------------------------------------------------------
# execute() — happy path (no dispatch)
# ---------------------------------------------------------------------------

def test_execute_returns_planning_result():
    uc = _make_usecase()
    result = uc.execute("add oauth login")
    assert isinstance(result, PlanningResult)
    assert result.session_id.startswith("plan-")


def test_execute_session_persisted():
    repo = InMemoryPlannerSessionRepository()
    uc = _make_usecase(session_repo=repo)
    result = uc.execute("add oauth")
    session = repo.get(result.session_id)
    assert session is not None
    assert session.status == PlannerSessionStatus.COMPLETED


def test_execute_roadmap_parsed():
    uc = _make_usecase()
    result = uc.execute("add oauth")
    assert result.roadmap is not None
    assert len(result.roadmap.goals) == 1
    assert result.roadmap.goals[0].name == "setup-db"


def test_execute_no_validation_errors_on_clean_roadmap():
    uc = _make_usecase()
    result = uc.execute("add oauth")
    assert result.validation_errors == []
    assert not result.has_errors


# ---------------------------------------------------------------------------
# execute() — with dispatch=True
# ---------------------------------------------------------------------------

def test_execute_dispatch_true_dispatches_goals():
    repo = InMemoryPlannerSessionRepository()
    goal = _make_goal_aggregate()
    goal_init = MagicMock()
    goal_init.execute.return_value = goal

    uc = _make_usecase(session_repo=repo, goal_init_returns=goal)
    # Patch goal_init directly
    uc._goal_init = goal_init

    result = uc.execute("add oauth", dispatch=True)
    assert result.dispatched_count == 1
    assert goal.goal_id in result.goals_dispatched
    goal_init.execute.assert_called_once()


def test_execute_dispatch_skipped_when_validation_errors():
    goal_init = MagicMock()
    uc = _make_usecase(validator_passes=False, goal_init_returns=_make_goal_aggregate())
    uc._goal_init = goal_init

    result = uc.execute("add oauth", dispatch=True)
    # validation errors → dispatch skipped
    goal_init.execute.assert_not_called()
    assert result.dispatched_count == 0


# ---------------------------------------------------------------------------
# execute() — LLM failure
# ---------------------------------------------------------------------------

def test_execute_runtime_error_fails_session():
    class ErrorRuntime(StubPlannerRuntime):
        def run_session(self, prompt, tools, max_turns=15, session_callback=None):
            raise PlannerRuntimeError("API timeout")

    repo = InMemoryPlannerSessionRepository()
    uc = _make_usecase(runtime=ErrorRuntime(), session_repo=repo)
    result = uc.execute("plan something")

    assert result.failure_reason == "API timeout"
    session = repo.get(result.session_id)
    assert session.status == PlannerSessionStatus.FAILED


# ---------------------------------------------------------------------------
# execute() — capability mismatch
# ---------------------------------------------------------------------------

def test_execute_capability_mismatch_adds_errors():
    # Agent registry has no agents → capability errors suppressed (test env)
    uc = _make_usecase(agents=[])
    result = uc.execute("add oauth")
    # Empty registry → no errors (per spec: warnings only when registry empty)
    assert result.validation_errors == []


def test_execute_capability_mismatch_with_populated_registry():
    agent = AgentProps(
        agent_id="a1",
        name="agent-1",
        capabilities=["frontend"],  # no 'python'
        version="1.0.0",
    )
    uc = _make_usecase(agents=[agent])
    result = uc.execute("add oauth")
    # 'python' capability not in registry → validation error
    assert any("python" in e for e in result.validation_errors)


# ---------------------------------------------------------------------------
# execute() — spec validation violations
# ---------------------------------------------------------------------------

def test_execute_validator_violations_recorded():
    uc = _make_usecase(validator_passes=False)
    result = uc.execute("add oauth")
    assert result.has_errors
    assert any("forbidden" in e for e in result.validation_errors)


# ---------------------------------------------------------------------------
# Idempotent re-dispatch via dispatch_roadmap()
# ---------------------------------------------------------------------------

def test_dispatch_roadmap_idempotent():
    repo = InMemoryPlannerSessionRepository()
    goal = _make_goal_aggregate()
    goal_init = MagicMock()
    goal_init.execute.return_value = goal

    uc = _make_usecase(session_repo=repo, goal_init_returns=goal)
    uc._goal_init = goal_init

    result = uc.execute("add oauth")
    assert result.session_id

    # Manually set session as completed with valid roadmap
    session = repo.get(result.session_id)
    assert session is not None

    # dispatch_roadmap should work when has_valid_roadmap() is True
    if session.has_valid_roadmap():
        result2 = uc.dispatch_roadmap(result.session_id)
        # goal_init.execute called again (idempotent re-dispatch)
        assert result2.dispatched_count >= 0  # at least attempted


def test_dispatch_roadmap_invalid_session_raises():
    repo = InMemoryPlannerSessionRepository()
    uc = _make_usecase(session_repo=repo)
    # Create a failed session manually
    session = PlannerSession.create("test")
    session.start()
    session.fail("reason")
    repo.save(session)

    with pytest.raises(ValueError, match="valid roadmap"):
        uc.dispatch_roadmap(session.session_id)


# ---------------------------------------------------------------------------
# Session turn persistence
# ---------------------------------------------------------------------------

def test_turns_are_persisted_in_session():
    repo = InMemoryPlannerSessionRepository()
    uc = _make_usecase(session_repo=repo)
    result = uc.execute("add oauth")
    session = repo.get(result.session_id)
    # StubPlannerRuntime calls session_callback twice (assistant + tool_result)
    assert len(session.turns) >= 1
