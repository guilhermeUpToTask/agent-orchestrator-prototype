from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.app.handlers.execution_handler import ExecutionHandler
from src.app.testing.fakes import (
    CollectingEventSink,
    FakeClock,
    InMemoryAgentRepository,
    InMemoryOutbox,
    InMemoryPlanRepository,
    InMemoryUnitOfWork,
)
from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.capability import Capability
from src.domain.entities.execution_contracts import (
    ContractCriterion,
    GoalContract,
    TaskContract,
    VerificationStrategy,
)
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import Cycle, PlanStatus
from src.domain.entities.task import Task
from src.domain.policies.retry_policies import RetryPolicy
from src.domain.value_objects.tasks_vos import TaskResult
from src.infra.git.workspace import GitBranchWorkspace
from src.infra.runtime.verification_executor import LocalVerificationExecutor

pytestmark = pytest.mark.integration
NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _agent(agent_id: str, capability: str) -> AgentSpec:
    return AgentSpec(
        id=agent_id,
        name=agent_id,
        role=capability,
        model_role="smart",
        instructions="",
        capabilities=[Capability(id=capability, name=capability, description="")],
        default_retry=RetryPolicy(),
    )


class WritingRunner:
    def __init__(self, main_repo: Path | None = None):
        self._main_repo = main_repo

    async def run(self, task, spec, *, idempotency_key, event_sink, workspace):
        root = Path(workspace.path)
        if spec.id == "test-author":
            tests = root / "tests"
            tests.mkdir(exist_ok=True)
            (tests / "test_feature.py").write_text(
                "from pathlib import Path\n\n"
                "def test_feature():\n"
                "    assert Path('feature.txt').read_text() == 'ready'\n"
            )
        else:
            (root / "feature.txt").write_text("ready")
            if self._main_repo is not None:
                (self._main_repo / "stray.txt").write_text("escaped")
        return TaskResult.success("agent claimed success")


class DeletingTestRunner:
    async def run(self, task, spec, *, idempotency_key, event_sink, workspace):
        (Path(workspace.path) / "tests" / "test_existing.py").unlink()
        return TaskResult.success("deleted a test")


@pytest.mark.parametrize("main_repo_write", [False, True])
def test_tdd_stages_and_branch_barriers_use_orchestrator_evidence(tmp_path, main_repo_write):
    repo_dir = tmp_path / "repo"
    workspace = GitBranchWorkspace(repo_dir)
    clock = FakeClock(NOW)
    plans = InMemoryPlanRepository(clock)
    outbox = InMemoryOutbox()
    uow = InMemoryUnitOfWork(plans, outbox)
    criterion = ContractCriterion(id="g-1", description="feature is ready")
    task_contract = TaskContract(
        id="task-1",
        position=0,
        objective="implement feature",
        acceptance_criteria=[ContractCriterion(id="t-1", description="feature file is ready")],
        goal_criterion_ids=["g-1"],
        allowed_scope=["feature.txt"],
        forbidden_scope=["tests/"],
        verification_commands=["pytest -q tests/test_feature.py"],
        verification_strategy=VerificationStrategy.TDD,
    )
    task = Task(
        id="task-1",
        name="implement feature",
        position=0,
        description="implement feature",
        contract=task_contract,
        role_agent_ids={
            "test_author": "test-author",
            "implementer": "implementer",
        },
    )
    goal = Goal(
        id="goal-1",
        name="goal",
        position=0,
        description="goal",
        tasks=[task],
        contract=GoalContract(
            id="goal-1",
            objective="goal",
            acceptance_criteria=[criterion],
            tasks=[task_contract],
            frozen_at=NOW,
        ),
    )
    cycle = Cycle(
        id="cycle-1",
        intent_proposal_id="intent-1",
        draft_id="draft-1",
        goals=[goal],
        started_at=NOW,
    )
    plan = Plan(
        id="plan-1",
        project_id="project-1",
        brief="brief",
        phase=PlanPhase.RUNNING,
        status=PlanStatus.RUNNING,
        cycles=[cycle],
    )
    plans.add(plan)
    agents = InMemoryAgentRepository(
        [
            _agent("test-author", "test_authoring"),
            _agent("implementer", "implementation"),
        ],
        default_id="implementer",
    )
    handler = ExecutionHandler(
        WritingRunner(repo_dir if main_repo_write else None),
        agents,
        workspace,
        CollectingEventSink(),
        clock,
        LocalVerificationExecutor(clock),
    )

    # Test-author run establishes RED and freezes a revision-bound bundle.
    assert asyncio.run(handler.handle(plan.id, plan, uow)).value == "continue"
    after_red = plans.get(plan.id)
    task_after_red = after_red.active_cycle.goals[0].tasks[0]  # type: ignore[union-attr]
    assert task_after_red.test_bundle is not None
    assert task_after_red.tdd_stage == "implementation"
    assert (
        subprocess.run(
            ["git", "-C", str(repo_dir), "show", "cycle/cycle-1:tests/test_feature.py"],
            capture_output=True,
        ).returncode
        != 0
    )

    # Implementer starts from the authoritative test commit. Its self-report is
    # ignored; independent pytest evidence is what completes and merges the task.
    implementer_signal = asyncio.run(handler.handle(plan.id, after_red, uow))
    after_impl = plans.get(plan.id)
    if main_repo_write:
        assert implementer_signal.value == "paused"
        assert after_impl.status == PlanStatus.BLOCKED
        assert after_impl.block is not None
        assert "stray paths: ['stray.txt']" in after_impl.block.explanation
        assert after_impl.block.kind == "execution_failure"
        failed_task = after_impl.active_cycle.goals[0].tasks[0]  # type: ignore[union-attr]
        assert failed_task.status.value == "failed"
        # attempt.failure_kind is populated only from RuntimeFailure metadata;
        # a bare TaskFailed records its kind on the task result instead.
        assert failed_task.result is not None
        assert failed_task.result.failure_kind is not None
        assert failed_task.result.failure_kind.value == "tool_error"
        # list_attempts ordering ties under FakeClock's fixed timestamps -
        # select the failed attempt by its message, not by position.
        attempts = uow.executions.list_attempts(plan.id)
        assert any("project main repository" in (a.safe_message or "") for a in attempts)
        return
    impl_task = after_impl.active_cycle.goals[0].tasks[0]  # type: ignore[union-attr]
    assert implementer_signal.value == "continue", (
        f"paused_reason={after_impl.paused_reason!r} "
        f"task_status={impl_task.status.value!r} "
        f"task_result={impl_task.result!r}"
    )
    verified = plans.get(plan.id)
    verified_task = verified.active_cycle.goals[0].tasks[0]  # type: ignore[union-attr]
    assert verified_task.status.value == "done"
    assert verified_task.verification_evidence
    assert verified_task.result.metadata["candidate_commit_sha"]
    assert _git(repo_dir, "show", "goal/goal-1:feature.txt") == "ready"
    assert (
        subprocess.run(
            ["git", "-C", str(repo_dir), "show", "cycle/cycle-1:feature.txt"],
            capture_output=True,
        ).returncode
        != 0
    )

    # Only the verified, complete goal is promoted to the cycle branch.
    assert asyncio.run(handler.handle(plan.id, verified, uow)).value == "continue"
    assert _git(repo_dir, "show", "cycle/cycle-1:feature.txt") == "ready"
    completed_goal = plans.get(plan.id).active_cycle.goals[0]  # type: ignore[union-attr]
    assert completed_goal.status.value == "done"


def test_tdd_exit_127_is_infrastructure_failure_and_does_not_freeze_bundle(tmp_path):
    repo_dir = tmp_path / "repo"
    workspace = GitBranchWorkspace(repo_dir)
    clock = FakeClock(NOW)
    plans = InMemoryPlanRepository(clock)
    uow = InMemoryUnitOfWork(plans, InMemoryOutbox())
    contract = TaskContract(
        id="task-1",
        position=0,
        objective="implement feature",
        acceptance_criteria=[ContractCriterion(id="t-1", description="feature is ready")],
        goal_criterion_ids=["g-1"],
        allowed_scope=["feature.txt"],
        forbidden_scope=["tests/"],
        verification_commands=["exit 127"],
        verification_strategy=VerificationStrategy.TDD,
    )
    task = Task(
        id="task-1",
        name="implement feature",
        position=0,
        description="implement feature",
        contract=contract,
        role_agent_ids={"test_author": "test-author", "implementer": "implementer"},
    )
    goal = Goal(
        id="goal-1",
        name="goal",
        position=0,
        description="goal",
        tasks=[task],
        contract=GoalContract(
            id="goal-1",
            objective="goal",
            acceptance_criteria=[ContractCriterion(id="g-1", description="feature is ready")],
            tasks=[contract],
            frozen_at=NOW,
        ),
    )
    plan = Plan(
        id="plan-1",
        project_id="project-1",
        brief="brief",
        phase=PlanPhase.RUNNING,
        status=PlanStatus.RUNNING,
        cycles=[
            Cycle(
                id="cycle-1",
                intent_proposal_id="intent-1",
                draft_id="draft-1",
                goals=[goal],
                started_at=NOW,
            )
        ],
    )
    plans.add(plan)
    agents = InMemoryAgentRepository(
        [_agent("test-author", "test_authoring"), _agent("implementer", "implementation")],
        default_id="implementer",
    )
    handler = ExecutionHandler(
        WritingRunner(),
        agents,
        workspace,
        CollectingEventSink(),
        clock,
        LocalVerificationExecutor(clock),
    )

    assert asyncio.run(handler.handle(plan.id, plan, uow)).value == "continue"
    after_failure = plans.get(plan.id)
    failed_task = after_failure.active_cycle.goals[0].tasks[0]  # type: ignore[union-attr]
    assert failed_task.test_bundle is None
    assert failed_task.status.value == "pending"
    attempt = uow.executions.list_attempts(plan.id)[0]
    assert attempt.failure_kind == "tool_error"
    assert attempt.safe_message is not None
    assert "exit 127" in attempt.safe_message
    assert "exit code 127" in attempt.safe_message
    assert "infrastructure failure" in attempt.safe_message


def test_deleted_test_file_becomes_a_recoverable_verification_block(tmp_path):
    repo_dir = tmp_path / "repo"
    subprocess.run(
        ["git", "init", "-b", "main", str(repo_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    tests = repo_dir / "tests"
    tests.mkdir()
    (tests / "test_existing.py").write_text("def test_existing():\n    assert True\n")
    _git(repo_dir, "add", "tests/test_existing.py")
    _git(
        repo_dir,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.test",
        "commit",
        "-m",
        "existing test",
    )

    clock = FakeClock(NOW)
    plans = InMemoryPlanRepository(clock)
    uow = InMemoryUnitOfWork(plans, InMemoryOutbox())
    criterion = ContractCriterion(id="g-1", description="existing behavior remains")
    contract = TaskContract(
        id="task-1",
        position=0,
        objective="preserve behavior",
        acceptance_criteria=[ContractCriterion(id="t-1", description="checked")],
        goal_criterion_ids=["g-1"],
        allowed_scope=["."],
        forbidden_scope=[".git/"],
        verification_commands=["git diff --check"],
        verification_strategy=VerificationStrategy.EXECUTABLE_CHECK,
    )
    task = Task(
        id="task-1",
        name="preserve behavior",
        position=0,
        description="preserve behavior",
        contract=contract,
        role_agent_ids={
            "test_author": "test-author",
            "implementer": "implementer",
        },
    )
    goal = Goal(
        id="goal-1",
        name="goal",
        position=0,
        description="goal",
        tasks=[task],
        contract=GoalContract(
            id="goal-1",
            objective="goal",
            acceptance_criteria=[criterion],
            tasks=[contract],
            frozen_at=NOW,
        ),
    )
    plan = Plan(
        id="plan-1",
        project_id="project-1",
        brief="brief",
        phase=PlanPhase.RUNNING,
        status=PlanStatus.RUNNING,
        cycles=[
            Cycle(
                id="cycle-1",
                intent_proposal_id="intent-1",
                draft_id="draft-1",
                goals=[goal],
                started_at=NOW,
            )
        ],
    )
    plans.add(plan)
    agents = InMemoryAgentRepository(
        [
            _agent("test-author", "test_authoring"),
            _agent("implementer", "implementation"),
        ],
        default_id="implementer",
    )
    handler = ExecutionHandler(
        DeletingTestRunner(),
        agents,
        GitBranchWorkspace(repo_dir),
        CollectingEventSink(),
        clock,
        LocalVerificationExecutor(clock),
    )

    signal = asyncio.run(handler.handle(plan.id, plan, uow))

    assert signal.value == "paused"
    blocked = plans.get(plan.id)
    assert blocked.status == PlanStatus.BLOCKED
    assert blocked.block is not None
    assert blocked.block.kind == "execution_failure"
    assert "deleted or renamed" in blocked.block.explanation
    failed_task = blocked.active_cycle.goals[0].tasks[0]  # type: ignore[union-attr]
    assert failed_task.status.value == "failed"
    assert uow.executions.list_open_attempts(plan.id) == []
