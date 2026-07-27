"""The bug-fix workflow: a failing test is already in the repo.

This is the shape two Tier 1 runs of happy-path-v1 died on, and it is the most
common way a developer hands work to an agent — an issue with a repro test, a red
CI job, a TDD handoff. The reasoner produced a correct contract both times; the
pipeline had nowhere to put it, because it assumed an agent always authors the
checks and read "the author wrote nothing" as failure.

The task's checks come from the contract's own `verification_commands`, which
name a concrete file. Not from a repository scan: a scan cannot tell this task's
checks from another task's, which is fatal on a multi-task goal.

The protected-set assertion below is the one that matters most. An earlier fix
recomputed the path list AFTER running the verification commands, which for an
empty authoring diff left `protected_file_hashes == {}` — a bundle protecting
nothing, letting the implementer rewrite the very test it must satisfy. That
version would have passed a naive "the task completed" assertion.
"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.app.testing.fakes import (
    CollectingEventSink,
    InMemoryPlanningArtifactStore,
    FakeClock,
    InMemoryAgentRepository,
    InMemoryOutbox,
    InMemoryPlanRepository,
    InMemoryUnitOfWork,
)
from src.app.handlers.execution_handler import ExecutionHandler
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

CHECK = "tests/test_greeter.py"
UNIMPLEMENTED = 'def greet(name):\n    raise NotImplementedError("implement me")\n'
IMPLEMENTED = 'def greet(name):\n    return f"Hello, {name}!"\n'
TEST_SOURCE = (
    "from greeter import greet\n\n\n"
    "def test_greet():\n"
    '    assert greet("Ada") == "Hello, Ada!"\n'
)


def _seed_repo(repo: Path) -> None:
    """A repository that already contains the failing check."""
    (repo / "tests").mkdir(parents=True)
    (repo / "greeter.py").write_text(UNIMPLEMENTED)
    (repo / CHECK).write_text(TEST_SOURCE)
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    for args in (
        ["config", "user.email", "t@example.test"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-m", "seed: failing check, no implementation"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


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


class _Runner:
    """Writes nothing while authoring; implements on the second stage.

    The empty authoring diff is the CORRECT behaviour when the check already
    exists — there is nothing to write — and is what the pipeline used to read as
    "test author produced no executable checks".
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, task, spec, *, idempotency_key, event_sink, workspace):
        self.calls.append(spec.id)
        if spec.id == "implementer":
            (Path(workspace.path) / "greeter.py").write_text(IMPLEMENTED)
        return TaskResult.success("agent claimed success")


def _plan_with_declared_check() -> tuple[Plan, Task]:
    task_contract = TaskContract(
        id="task-1",
        position=0,
        objective="implement greet",
        acceptance_criteria=[ContractCriterion(id="t-1", description="greet returns a greeting")],
        goal_criterion_ids=["g-1"],
        allowed_scope=["greeter.py"],
        # The check exists, so the agent must not touch it. This is the contract
        # shape the real reasoner produced, and v1's pipeline rejected it.
        forbidden_scope=["tests/"],
        verification_commands=[f"python -m pytest -q {CHECK}"],
        verification_strategy=VerificationStrategy.EXECUTABLE_CHECK,
    )
    task = Task(
        id="task-1",
        name="implement greet",
        position=0,
        description="implement greet",
        contract=task_contract,
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
            acceptance_criteria=[ContractCriterion(id="g-1", description="greet works")],
            tasks=[task_contract],
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
    return plan, task


def test_a_declared_pre_existing_check_drives_both_stages(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    _seed_repo(repo_dir)
    clock = FakeClock(NOW)
    plans = InMemoryPlanRepository(clock)
    uow = InMemoryUnitOfWork(plans, InMemoryOutbox())
    plan, _ = _plan_with_declared_check()
    plans.add(plan)
    runner = _Runner()
    handler = ExecutionHandler(
        runner,
        InMemoryAgentRepository(
            [_agent("test-author", "test_authoring"), _agent("implementer", "implementation")],
            default_id="implementer",
        ),
        GitBranchWorkspace(repo_dir),
        CollectingEventSink(),
        clock,
        LocalVerificationExecutor(clock),
    )

    # Stage 1 — NO agent runs at all. The contract names a check that is already
    # here, so there is nothing to author and the orchestrator freezes it directly.
    assert asyncio.run(handler.handle(plan.id, plan, uow)).value == "continue"
    assert runner.calls == [], f"the authoring stage spent an agent call: {runner.calls}"
    after_red = plans.get(plan.id)
    task_after_red = after_red.active_cycle.goals[0].tasks[0]  # type: ignore[union-attr]
    bundle = task_after_red.test_bundle
    assert bundle is not None, "the declared check should have frozen a bundle"

    # THE regression: the protected set must hold the declared check. The bug
    # this replaces produced `{}` here, which protects nothing.
    assert CHECK in bundle.protected_file_hashes, (
        f"the pre-existing check is unprotected: {bundle.protected_file_hashes}"
    )
    # And the mapping records THIS task's check, not "every changed path".
    assert bundle.criterion_to_tests == {"t-1": [CHECK]}

    # Stage 2 — the implementer turns the declared check green.
    assert asyncio.run(handler.handle(plan.id, after_red, uow)).value == "continue"
    done = plans.get(plan.id)
    finished = done.active_cycle.goals[0].tasks[0]  # type: ignore[union-attr]
    assert finished.status.value == "done"
    assert finished.verification_evidence
    assert all(item.accepted for item in finished.verification_evidence)
    assert runner.calls == ["implementer"], "only the implementation stage needed an agent"


def test_an_already_green_check_is_rejected_as_non_discriminating(tmp_path: Path) -> None:
    """A check that already passes proves nothing about this task: the green
    after the work would be the same green as before it. Rejected loudly rather
    than accepted silently — a silent accept lets an agent do nothing and still
    be recorded as verified."""
    repo_dir = tmp_path / "repo"
    _seed_repo(repo_dir)
    (repo_dir / "greeter.py").write_text(IMPLEMENTED)  # already done
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-am", "already"], check=True,
                   capture_output=True)
    clock = FakeClock(NOW)
    plans = InMemoryPlanRepository(clock)
    uow = InMemoryUnitOfWork(plans, InMemoryOutbox())
    plan, _ = _plan_with_declared_check()
    plans.add(plan)
    handler = ExecutionHandler(
        _Runner(),
        InMemoryAgentRepository(
            [_agent("test-author", "test_authoring"), _agent("implementer", "implementation")],
            default_id="implementer",
        ),
        GitBranchWorkspace(repo_dir),
        CollectingEventSink(),
        clock,
        LocalVerificationExecutor(clock),
    )

    asyncio.run(handler.handle(plan.id, plan, uow))

    after = plans.get(plan.id)
    rejected = after.active_cycle.goals[0].tasks[0]  # type: ignore[union-attr]
    assert rejected.test_bundle is None, "a non-discriminating check must not freeze"
    assert rejected.status.value != "done"
    # The attempt is retryable, so the task sits in backoff rather than carrying
    # a terminal result; the reason lives on the attempt ledger.
    assert rejected.retry_not_before is not None
    reasons = [
        item.safe_message
        for item in uow.executions.list_attempts(plan.id)
        if item.safe_message
    ]
    assert any("did not establish a meaningful RED result" in reason for reason in reasons), reasons


class _AuthorRewritesAnotherTasksCheck:
    """An author that edits a check it did not write — the multi-task attack."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, task, spec, *, idempotency_key, event_sink, workspace):
        self.calls.append(spec.id)
        # Rewrite the pre-existing check into something trivially failing, which
        # would satisfy the RED-baseline rule and let the implementer "fix" it.
        (Path(workspace.path) / CHECK).write_text(
            "def test_greet():\n    assert False\n"
        )
        return TaskResult.success("agent claimed success")


def test_an_author_cannot_claim_another_tasks_check_by_rewriting_it(tmp_path: Path) -> None:
    """On a multi-task goal the earlier tasks' checks are already on the goal
    branch. Before the pre-authoring snapshot, an author could rewrite one into a
    trivially failing test, have it hashed as THIS task's protected evidence, and
    let the implementer make it pass — destroying the other task's verification
    while every gate downstream reported green."""
    repo_dir = tmp_path / "repo"
    _seed_repo(repo_dir)
    clock = FakeClock(NOW)
    plans = InMemoryPlanRepository(clock)
    uow = InMemoryUnitOfWork(plans, InMemoryOutbox())
    plan, _ = _plan_with_declared_check()
    # An UNSCOPED command declares nothing, so the author stage really runs — the
    # only path on which this attack is reachable at all. (With a declared check
    # the orchestrator freezes it and no agent is invoked.)
    contract = plan.cycles[0].goals[0].tasks[0].contract
    assert contract is not None
    plan.cycles[0].goals[0].tasks[0].contract = contract.model_copy(
        update={"verification_commands": ["python -m pytest -q"]}
    )
    plans.add(plan)
    handler = ExecutionHandler(
        _AuthorRewritesAnotherTasksCheck(),
        InMemoryAgentRepository(
            [_agent("test-author", "test_authoring"), _agent("implementer", "implementation")],
            default_id="implementer",
        ),
        GitBranchWorkspace(repo_dir),
        CollectingEventSink(),
        clock,
        LocalVerificationExecutor(clock),
    )

    asyncio.run(handler.handle(plan.id, plan, uow))

    after = plans.get(plan.id)
    task = after.active_cycle.goals[0].tasks[0]  # type: ignore[union-attr]
    assert task.test_bundle is None, "a rewritten foreign check must not freeze"
    reasons = [
        item.safe_message
        for item in uow.executions.list_attempts(plan.id)
        if item.safe_message
    ]
    assert any("protected test changed" in reason for reason in reasons), reasons


def test_the_bundle_protects_checks_this_task_did_not_write(tmp_path: Path) -> None:
    """Regression protection, free: every check that existed before the task is in
    `protected_file_hashes`, so the IMPLEMENTER cannot weaken another task's test
    either — `validate_candidate` already hash-checks every entry."""
    repo_dir = tmp_path / "repo"
    _seed_repo(repo_dir)
    # A second check, belonging to some other task.
    (repo_dir / "tests" / "test_other.py").write_text("def test_other():\n    assert True\n")
    subprocess.run(["git", "-C", str(repo_dir), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-m", "another task's check"],
        check=True, capture_output=True,
    )
    clock = FakeClock(NOW)
    plans = InMemoryPlanRepository(clock)
    uow = InMemoryUnitOfWork(plans, InMemoryOutbox())
    plan, _ = _plan_with_declared_check()
    plans.add(plan)
    handler = ExecutionHandler(
        _Runner(),
        InMemoryAgentRepository(
            [_agent("test-author", "test_authoring"), _agent("implementer", "implementation")],
            default_id="implementer",
        ),
        GitBranchWorkspace(repo_dir),
        CollectingEventSink(),
        clock,
        LocalVerificationExecutor(clock),
    )

    asyncio.run(handler.handle(plan.id, plan, uow))

    bundle = plans.get(plan.id).active_cycle.goals[0].tasks[0].test_bundle  # type: ignore[union-attr]
    assert bundle is not None
    assert "tests/test_other.py" in bundle.protected_file_hashes, (
        "another task's check must be protected against the implementer"
    )
    # ...but it is NOT claimed as this task's evidence.
    assert bundle.criterion_to_tests == {"t-1": [CHECK]}


def test_a_verification_command_cannot_rewrite_another_tasks_check(tmp_path: Path) -> None:
    """The same guard, applied AFTER the command runs.

    `verification_commands` is arbitrary shell from the contract. Until it shared
    the authoring guard it was judged by `is_check_path` alone, so a command could
    rewrite another task's check during the baseline run and have the result
    frozen as this task's evidence — the agent never had to touch it.
    """
    repo_dir = tmp_path / "repo"
    _seed_repo(repo_dir)
    victim = "tests/test_other.py"
    (repo_dir / victim).write_text("def test_other():\n    assert True\n")
    for args in (["add", "-A"], ["commit", "-m", "another task's check"]):
        subprocess.run(["git", "-C", str(repo_dir), *args], check=True, capture_output=True)

    clock = FakeClock(NOW)
    plans = InMemoryPlanRepository(clock)
    uow = InMemoryUnitOfWork(plans, InMemoryOutbox())
    plan, _ = _plan_with_declared_check()
    # A command that clobbers a check belonging to someone else.
    contract = plan.cycles[0].goals[0].tasks[0].contract
    assert contract is not None
    plan.cycles[0].goals[0].tasks[0].contract = contract.model_copy(
        update={
            "verification_commands": [
                f"printf 'def test_other():\\n    assert False\\n' > {victim}; "
                f"python -m pytest -q {CHECK}"
            ]
        }
    )
    plans.add(plan)
    handler = ExecutionHandler(
        _Runner(),
        InMemoryAgentRepository(
            [_agent("test-author", "test_authoring"), _agent("implementer", "implementation")],
            default_id="implementer",
        ),
        GitBranchWorkspace(repo_dir),
        CollectingEventSink(),
        clock,
        LocalVerificationExecutor(clock),
    )

    asyncio.run(handler.handle(plan.id, plan, uow))

    after = plans.get(plan.id)
    task = after.active_cycle.goals[0].tasks[0]  # type: ignore[union-attr]
    assert task.test_bundle is None, "a clobbered foreign check must not freeze"
    reasons = [
        item.safe_message
        for item in uow.executions.list_attempts(plan.id)
        if item.safe_message
    ]
    assert any(
        "verification command violated the frozen checks" in reason for reason in reasons
    ), reasons


def test_the_baseline_verdict_is_recorded(tmp_path: Path) -> None:
    """Whether the checks were RED or GREEN before the work decides whether the
    green afterwards means anything, and it was stored nowhere queryable:
    `baseline_evidence_refs` is always empty and `red_or_baseline_evidence_refs`
    holds only sha256 digests of output — enough to prove a command ran, not what
    it decided."""
    repo_dir = tmp_path / "repo"
    _seed_repo(repo_dir)
    clock = FakeClock(NOW)
    plans = InMemoryPlanRepository(clock)
    uow = InMemoryUnitOfWork(plans, InMemoryOutbox())
    plan, _ = _plan_with_declared_check()
    plans.add(plan)
    artifacts = InMemoryPlanningArtifactStore()
    handler = ExecutionHandler(
        _Runner(),
        InMemoryAgentRepository(
            [_agent("test-author", "test_authoring"), _agent("implementer", "implementation")],
            default_id="implementer",
        ),
        GitBranchWorkspace(repo_dir),
        CollectingEventSink(),
        clock,
        LocalVerificationExecutor(clock),
        planning_artifacts=artifacts,
    )

    asyncio.run(handler.handle(plan.id, plan, uow))

    recorded = artifacts.latest(plan.id, "verification_baseline", goal_id="goal-1")
    assert recorded, "the baseline verdict was not recorded"
    payload = recorded[-1].payload or {}
    assert payload["verdict"] == "red", payload
    assert payload["checks"] == [CHECK]
    assert payload["exit_codes"] and all(code != 0 for code in payload["exit_codes"])
