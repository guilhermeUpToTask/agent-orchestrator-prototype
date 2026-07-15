from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.app.handlers.execution_handler import ExecutionHandler
from src.app.ports import CommandExecution
from src.app.testing.fakes import (
    CollectingEventSink,
    FakeClock,
    InMemoryAgentRepository,
    InMemoryOutbox,
    InMemoryPlanRepository,
    InMemoryUnitOfWork,
)
from src.app.use_cases.cyclic_planning import propose_intent
from src.app.verification import sha256_file
from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.execution_contracts import (
    ContractCriterion,
    TaskContract,
    TestBundle as AuthoritativeTestBundle,
    VerificationEvidence,
    VerificationKind,
    VerificationStrategy,
)
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import (
    Cycle,
    CycleStatus,
    PlanStatus,
    ProposalKind,
)
from src.domain.entities.task import Task
from src.domain.errors.planning_errors import InvalidEditError
from src.domain.policies.retry_policies import RetryPolicy
from src.domain.value_objects.lifecycle import Status
from src.domain.value_objects.tasks_vos import TaskResult


NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def _agent() -> AgentSpec:
    return AgentSpec(
        id="agent-1",
        name="agent",
        role="implementation",
        model_role="smart",
        instructions="",
        capabilities=[],
        default_retry=RetryPolicy(),
    )


def _environment(task: Task, *, goal_status: Status = Status.PENDING):
    goal = Goal(
        id="goal-1",
        name="goal",
        position=0,
        description="goal",
        status=goal_status,
        tasks=[task],
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
        brief="ship safely",
        phase=PlanPhase.RUNNING,
        status=PlanStatus.RUNNING,
        cycles=[cycle],
    )
    clock = FakeClock(NOW)
    plans = InMemoryPlanRepository(clock)
    plans.add(plan)
    uow = InMemoryUnitOfWork(plans, InMemoryOutbox())
    agents = InMemoryAgentRepository([_agent()], default_id="agent-1")
    return plan, plans, uow, agents, clock


class SuccessfulRunner:
    async def run(self, task, spec, *, idempotency_key, event_sink, workspace):
        return TaskResult.success("done")


@dataclass
class Handle:
    path: str
    base_ref: str | None = None


class RecordingWorkspace:
    def __init__(self, path: Path) -> None:
        self.handle = Handle(str(path))
        self.commits = 0
        self.discards = 0

    async def begin(self, *args, **kwargs):
        return self.handle

    async def snapshot(self, handle):
        return "candidate"

    async def checkpoint(self, handle):
        return "checkpoint"

    async def merge_goal(self, plan_id, cycle_id, goal_id):
        return "goal-commit"

    async def commit(self, handle):
        self.commits += 1

    async def discard(self, handle):
        self.discards += 1


class MutatingVerifier:
    def __init__(self) -> None:
        self.changed_calls = 0

    async def changed_paths(self, workspace_path, base_ref=None):
        self.changed_calls += 1
        if self.changed_calls == 1:
            return ["src/feature.py"]
        return ["src/feature.py", "pyproject.toml"]

    async def run(self, workspace_path, commands):
        return [
            CommandExecution(
                command=commands[0],
                exit_code=0,
                started_at=NOW,
                finished_at=NOW,
                bounded_output_ref="sha256:ok",
            )
        ]


def test_verification_commands_cannot_mutate_the_validated_tree(tmp_path) -> None:
    protected = tmp_path / "tests" / "test_feature.py"
    protected.parent.mkdir()
    protected.write_text("def test_feature():\n    assert True\n")
    contract = TaskContract(
        id="task-1",
        position=0,
        objective="feature",
        acceptance_criteria=[ContractCriterion(id="t-1", description="works")],
        goal_criterion_ids=["g-1"],
        allowed_scope=["src/"],
        verification_commands=["pytest -q"],
        verification_strategy=VerificationStrategy.TDD,
    )
    bundle = AuthoritativeTestBundle(
        task_id="task-1",
        task_revision=1,
        test_commit_sha="tests-commit",
        protected_file_hashes={"tests/test_feature.py": sha256_file(protected)},
        criterion_to_tests={"t-1": ["tests/test_feature.py"]},
        verification_strategy=VerificationStrategy.TDD,
        red_or_baseline_evidence_refs=["sha256:red"],
        frozen_at=NOW,
    )
    task = Task(
        id="task-1",
        name="task",
        position=0,
        description="task",
        agent_id="agent-1",
        contract=contract,
        test_bundle=bundle,
    )
    plan, plans, uow, agents, clock = _environment(task)
    workspace = RecordingWorkspace(tmp_path)
    verifier = MutatingVerifier()
    handler = ExecutionHandler(
        SuccessfulRunner(), agents, workspace, CollectingEventSink(), clock, verifier
    )

    asyncio.run(handler.handle(plan.id, plan, uow))

    assert verifier.changed_calls == 2
    assert workspace.commits == 0
    assert workspace.discards == 1
    assert plans.get(plan.id).promotion_reservation is None


class ReplanRacingWorkspace(RecordingWorkspace):
    def __init__(self, path: Path, uow: InMemoryUnitOfWork, clock: FakeClock) -> None:
        super().__init__(path)
        self.uow = uow
        self.clock = clock
        self.replan_rejected = False

    async def commit(self, handle):
        try:
            propose_intent(
                "plan-1",
                objective="replace active work",
                scope=[],
                constraints=[],
                exclusions=[],
                kind=ProposalKind.REPLAN,
                planner_session_ref=None,
                uow=self.uow,
                clock=self.clock,
            )
        except InvalidEditError:
            self.replan_rejected = True
        await super().commit(handle)


def test_candidate_promotion_reservation_rejects_racing_replan(tmp_path) -> None:
    task = Task(
        id="task-1",
        name="task",
        position=0,
        description="task",
        agent_id="agent-1",
    )
    plan, plans, uow, agents, clock = _environment(task)
    workspace = ReplanRacingWorkspace(tmp_path, uow, clock)
    handler = ExecutionHandler(SuccessfulRunner(), agents, workspace, CollectingEventSink(), clock)

    assert asyncio.run(handler.handle(plan.id, plan, uow)).value == "continue"
    stored = plans.get(plan.id)
    assert workspace.replan_rejected
    assert workspace.commits == 1
    assert stored.promotion_reservation is None
    assert stored.active_cycle is not None
    assert stored.active_cycle.goals[0].tasks[0].status == Status.DONE


class SupersedingRunner:
    def __init__(self, uow: InMemoryUnitOfWork, clock: FakeClock) -> None:
        self.uow = uow
        self.clock = clock

    async def run(self, task, spec, *, idempotency_key, event_sink, workspace):
        with self.uow:
            plan = self.uow.plans.get("plan-1")
            assert plan.active_cycle is not None
            old_cycle = plan.active_cycle
            old_cycle.status = CycleStatus.SUPERSEDED
            old_cycle.superseded_at = self.clock.now()
            plan.cycles.append(
                Cycle(
                    id="cycle-2",
                    intent_proposal_id="intent-2",
                    draft_id="draft-2",
                    goals=[],
                    started_at=self.clock.now(),
                )
            )
            plan.bump_version()
            self.uow.plans.save(plan)
        return TaskResult.success("late")


def test_superseded_cycle_attempt_is_abandoned_and_ledger_closes(tmp_path) -> None:
    task = Task(
        id="task-1",
        name="task",
        position=0,
        description="task",
        agent_id="agent-1",
    )
    plan, plans, uow, agents, clock = _environment(task)
    handler = ExecutionHandler(
        SupersedingRunner(uow, clock),
        agents,
        RecordingWorkspace(tmp_path),
        CollectingEventSink(),
        clock,
    )

    assert asyncio.run(handler.handle(plan.id, plan, uow)).value == "paused"
    stored = plans.get(plan.id)
    old_cycle = next(cycle for cycle in stored.cycles if cycle.id == "cycle-1")
    assert old_cycle.goals[0].tasks[0].status == Status.SKIPPED
    assert uow.executions.list_open_attempts(plan.id) == []


class FailingGoalWorkspace(RecordingWorkspace):
    def __init__(self, path: Path, uow: InMemoryUnitOfWork) -> None:
        super().__init__(path)
        self.uow = uow
        self.called_outside_transaction = False

    async def merge_goal(self, plan_id, cycle_id, goal_id):
        self.called_outside_transaction = self.uow.executions._tx_runs is None
        raise RuntimeError("merge conflict")


def test_goal_merge_runs_outside_transaction_and_conflict_blocks_plan(tmp_path) -> None:
    evidence = VerificationEvidence(
        id="evidence-1",
        task_id="task-1",
        task_revision=1,
        run_id="run-1",
        candidate_commit_sha="candidate",
        test_commit_sha="tests",
        exact_command="pytest",
        exit_code=0,
        started_at=NOW,
        finished_at=NOW,
        bounded_output_ref="sha256:ok",
        verification_kind=VerificationKind.AUTHORITATIVE_TEST,
        accepted=True,
    )
    task = Task(
        id="task-1",
        name="task",
        position=0,
        description="task",
        agent_id="agent-1",
        status=Status.DONE,
        result=TaskResult.success("done"),
        verification_evidence=[evidence],
    )
    plan, plans, uow, agents, clock = _environment(task, goal_status=Status.RUNNING)
    workspace = FailingGoalWorkspace(tmp_path, uow)
    handler = ExecutionHandler(SuccessfulRunner(), agents, workspace, CollectingEventSink(), clock)

    assert asyncio.run(handler.handle(plan.id, plan, uow)).value == "paused"
    stored = plans.get(plan.id)
    assert workspace.called_outside_transaction
    assert stored.promotion_reservation is None
    assert stored.status == PlanStatus.BLOCKED
    assert stored.block is not None
    assert stored.block.kind == "goal_promotion_failure"
