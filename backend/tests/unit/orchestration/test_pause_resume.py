"""The human pause gate + the manual retry (un-freeze #3), on both backends via
env_factory: the claim predicate must skip a paused plan identically on the fake
and the real SQLite, the auto-pause must land in the same transaction as the
terminal task failure, while resume and targeted retry remain separate commands.
A human retry bypasses should_retry and starts a fresh policy cycle for one task.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import (
    Cycle,
    PlanBlock,
    PlanStatus,
)
from src.domain.entities.task import Task
from src.domain.errors.tasks_errors import InvalidTransitionError
from src.domain.policies.retry_policies import RetryPolicy
from src.domain.value_objects.lifecycle import FailureKind, Status

from src.app.execution_records import RuntimeCircuit
from src.app.handlers.base import Signal
from src.app.handlers.execution_handler import ExecutionHandler
from src.app.testing.fakes import DummyBehavior, InMemoryCapabilityRepository
from src.app.use_cases.apply_edit import UpdateTask, apply_edit
from src.app.use_cases.advance_plan import advance_plan
from src.app.use_cases.control import finish_review
from src.app.use_cases.pause_resume import (
    pause_plan,
    resume_plan,
    retry_planning_stage,
    retry_task,
)


def task(tid: str, position: int, **kwargs) -> Task:
    return Task(id=tid, name=tid, position=position, description="", agent_id="a1", **kwargs)


def running_plan(tasks: list[Task] | None = None, retry_max: int = 3) -> Plan:
    return Plan(
        project_id="project-1",
        id="p1",
        brief="b",
        phase=PlanPhase.RUNNING,
        retry_policy=RetryPolicy(max_attempts=retry_max),
        goals=[
            Goal(
                id="g1",
                name="g1",
                position=0,
                description="",
                tasks=tasks if tasks is not None else [task("t0", 0)],
            )
        ],
    )


async def drive(env, max_steps: int = 50) -> str:
    signal = "continue"
    steps = 0
    while signal in ("continue", "not_ready") and steps < max_steps:
        signal = await advance_plan("p1", *env.args)
        if signal == "not_ready":
            env.clock.advance(120)
        steps += 1
    return signal


# ---- the claim gate ----
def test_paused_plan_is_never_claimed(env_factory):
    env = env_factory()
    env.seed(running_plan())
    pause_plan("p1", env.uow, "operator pause")

    assert env.uow.plans.claim_one_unit("w1", lease_seconds=60) is None


def test_pause_survives_lease_expiry_and_time(env_factory):
    """The pause gate is durable state, not a lease: no amount of clock advance
    (dead worker, restart) makes a paused plan claimable."""
    env = env_factory()
    env.seed(running_plan())
    pause_plan("p1", env.uow, None)

    env.clock.advance(3600)
    assert env.uow.plans.claim_one_unit("w1", lease_seconds=60) is None

    resume_plan("p1", env.uow)
    claimed = env.uow.plans.claim_one_unit("w1", lease_seconds=60)
    assert claimed is not None and claimed.id == "p1"


def test_advance_on_paused_plan_dispatches_nothing(env_factory):
    env = env_factory()
    env.seed(running_plan())
    pause_plan("p1", env.uow, None)

    signal = asyncio.run(advance_plan("p1", *env.args))

    assert signal == "paused"
    assert env.runner.calls == {}  # no task ran


@pytest.mark.parametrize("goal_scoped", [False, True])
@pytest.mark.parametrize("blocked_state", ["status", "scalar"])
def test_execution_handler_stops_beneath_plan_wide_block(
    env_factory, goal_scoped, blocked_state
):
    env = env_factory()
    plan = running_plan()
    if blocked_state == "status":
        plan.status = PlanStatus.BLOCKED
    else:
        plan.block = PlanBlock(
            id="block-1",
            kind="operator_stop",
            explanation="operator intervention required",
            stage="implementation",
            legal_resolutions=["start_replan"],
            created_at=env.clock.now(),
        )
    env.seed(plan)
    execution = ExecutionHandler(env.runner, env.agents, env.ws, env.sink, env.clock)
    stored = env.stored("p1")

    if goal_scoped:
        signal = asyncio.run(execution.handle_goal("p1", "g1", stored, env.uow))
    else:
        signal = asyncio.run(execution.handle("p1", stored, env.uow))

    assert signal == Signal.PAUSED
    assert env.runner.calls == {}


# ---- pause command semantics ----
def test_pause_rejected_outside_worker_claimable_phases(env_factory):
    env = env_factory()
    plan = running_plan()
    plan.phase = PlanPhase.AWAITING_REVIEW  # gates are already paused by design
    env.seed(plan)

    with pytest.raises(InvalidTransitionError):
        pause_plan("p1", env.uow, None)


def test_pause_is_idempotent(env_factory):
    env = env_factory()
    env.seed(running_plan())
    pause_plan("p1", env.uow, "first")
    pause_plan("p1", env.uow, "second")  # no-op: no bump, no second event

    assert env.outbox_types().count("PlanPaused") == 1
    assert env.stored("p1").paused


def test_resume_unpaused_raises_invalid_transition(env_factory):
    env = env_factory()
    env.seed(running_plan())

    with pytest.raises(InvalidTransitionError):
        resume_plan("p1", env.uow)


# ---- resume and retry are separate commands ----
def test_resume_changes_availability_only(env_factory):
    env = env_factory()
    failed = task("t0", 0, status=Status.FAILED, attempt=3, cycle_attempt=3)
    gated = task("t1", 1, retry_not_before=env.clock.now() + timedelta(seconds=300))
    plan = running_plan(tasks=[failed, gated])
    plan.goals[0].status = Status.RUNNING
    plan.paused = True
    plan.paused_reason = "task t0 failed"
    env.seed(plan)

    resume_plan("p1", env.uow)

    stored = env.stored("p1")
    assert not stored.paused and stored.paused_reason is None
    t0, t1 = stored.goals[0].tasks
    assert t0.status == Status.FAILED and t0.attempt == 3
    assert t1.retry_not_before is not None
    assert env.outbox_types() == ["PlanResumed"]


def test_targeted_retry_preserves_absolute_attempt_and_unrelated_gates(env_factory):
    env = env_factory()
    failed = task("t0", 0, status=Status.FAILED, attempt=3, cycle_attempt=3)
    gated = task("t1", 1, retry_not_before=env.clock.now() + timedelta(seconds=300))
    plan = running_plan(tasks=[failed, gated])
    plan.goals[0].status = Status.RUNNING
    plan.paused = True
    env.seed(plan)

    retry_task("p1", "g1", "t0", env.uow, env.clock)

    stored = env.stored("p1")
    t0, t1 = stored.goals[0].tasks
    assert t0.status == Status.PENDING
    assert t0.attempt == 3 and t0.cycle_attempt == 0 and t0.retry_cycle == 1
    assert t1.retry_not_before is not None
    assert env.outbox_types() == ["TaskRetried"]


def test_blocked_targeted_retry_resolves_only_the_selected_task(env_factory):
    env = env_factory()
    failed = task("t0", 0, status=Status.FAILED, attempt=3, cycle_attempt=3)
    goal = Goal(
        id="g1",
        name="g1",
        position=0,
        description="",
        status=Status.RUNNING,
        tasks=[failed],
    )
    cycle = Cycle(
        id="cycle-1",
        intent_proposal_id="intent-1",
        draft_id="draft-1",
        goals=[goal],
        started_at=env.clock.now(),
    )
    plan = Plan(
        project_id="project-1",
        id="p1",
        brief="b",
        status=PlanStatus.BLOCKED,
        cycles=[cycle],
        block=PlanBlock(
            id="block-1",
            kind="execution_failure",
            explanation="failed",
            stage="implementation",
            goal_id="g1",
            task_id="t0",
            legal_resolutions=["retry_stage", "edit_task", "start_replan"],
            created_at=env.clock.now(),
        ),
    )
    env.seed(plan)

    retry_task("p1", "g1", "t0", env.uow, env.clock)

    stored = env.stored("p1")
    assert stored.status == PlanStatus.RUNNING
    assert stored.block is not None and stored.block.resolution == "retry_stage"
    retried = stored.active_cycle.goals[0].tasks[0]
    assert retried.status == Status.PENDING
    assert retried.attempt == 3 and retried.retry_cycle == 1
    assert env.outbox_types() == ["TaskRetried", "BlockResolved"]


def test_blocked_task_can_be_edited_then_continued_without_double_retry(env_factory):
    env = env_factory()
    failed = task("t0", 0, status=Status.FAILED, attempt=3, cycle_attempt=3)
    goal = Goal(
        id="g1",
        name="g1",
        position=0,
        description="",
        status=Status.RUNNING,
        tasks=[failed],
    )
    plan = Plan(
        project_id="project-1",
        id="p1",
        brief="b",
        status=PlanStatus.BLOCKED,
        cycles=[
            Cycle(
                id="cycle-1",
                intent_proposal_id="intent-1",
                draft_id="draft-1",
                goals=[goal],
                started_at=env.clock.now(),
            )
        ],
        block=PlanBlock(
            id="block-1",
            kind="execution_failure",
            explanation="failed",
            stage="implementation",
            goal_id="g1",
            task_id="t0",
            legal_resolutions=["retry_stage", "edit_task", "start_replan"],
            created_at=env.clock.now(),
        ),
    )
    env.seed(plan)

    apply_edit(
        "p1",
        UpdateTask(goal_id="g1", task_id="t0", name="corrected task"),
        env.uow,
        InMemoryCapabilityRepository(),
        env.agents,
    )
    edited = env.stored("p1").active_cycle.goals[0].tasks[0]
    assert edited.status == Status.PENDING
    assert edited.revision == 2

    retry_task("p1", "g1", "t0", env.uow, env.clock)

    stored = env.stored("p1")
    continued = stored.active_cycle.goals[0].tasks[0]
    assert stored.status == PlanStatus.RUNNING
    assert stored.block is not None and stored.block.resolution == "edit_task"
    assert continued.status == Status.PENDING
    assert continued.retry_cycle == 0
    assert env.outbox_types() == ["BlockResolved"]


def test_provider_wait_and_retry_clears_the_runtime_circuit(env_factory):
    env = env_factory()
    failed = task("t0", 0, status=Status.FAILED)
    goal = Goal(
        id="g1",
        name="g1",
        position=0,
        description="",
        status=Status.RUNNING,
        tasks=[failed],
    )
    cycle = Cycle(
        id="cycle-1",
        intent_proposal_id="intent-1",
        draft_id="draft-1",
        goals=[goal],
        started_at=env.clock.now(),
    )
    plan = Plan(
        project_id="project-1",
        id="p1",
        brief="b",
        status=PlanStatus.BLOCKED,
        cycles=[cycle],
        block=PlanBlock(
            id="block-1",
            kind="provider_capacity",
            explanation="quota",
            stage="implementation",
            goal_id="g1",
            task_id="t0",
            legal_resolutions=["wait_and_retry", "edit_task", "start_replan"],
            evidence_refs=["runtime-circuit://pi/provider/model"],
            created_at=env.clock.now(),
        ),
    )
    env.seed(plan)
    with env.uow:
        env.uow.executions.upsert_runtime_circuit(
            RuntimeCircuit(
                runtime="pi",
                provider_id="provider",
                model_id="model",
                failure_count=3,
                opened_at=env.clock.now(),
                retry_at=env.clock.now() + timedelta(hours=1),
                last_failure_kind="rate_limit",
                safe_message="quota",
                manual_intervention=True,
            )
        )

    retry_task("p1", "g1", "t0", env.uow, env.clock)

    with env.uow:
        circuit = env.uow.executions.get_runtime_circuit("pi", "provider", "model")
    assert circuit is None
    stored = env.stored("p1")
    assert stored.block is not None and stored.block.resolution == "wait_and_retry"
    assert stored.active_cycle.goals[0].tasks[0].status == Status.PENDING


def test_retry_planning_stage_resolves_reasoner_block(env_factory):
    env = env_factory()
    plan = Plan(
        project_id="project-1",
        id="p1",
        brief="b",
        status=PlanStatus.BLOCKED,
        planning_attempts=3,
        planning_retry_not_before=env.clock.now() + timedelta(hours=1),
        block=PlanBlock(
            id="block-1",
            kind="reasoner_failure",
            explanation="planner unavailable",
            stage="cycle_architecture",
            legal_resolutions=["retry_stage", "start_replan"],
            created_at=env.clock.now(),
        ),
    )
    env.seed(plan)

    retry_planning_stage("p1", env.uow, env.clock, env.agents)

    stored = env.stored("p1")
    assert stored.status == PlanStatus.RUNNING
    assert stored.planning_attempts == 0
    assert stored.planning_retry_not_before is None
    assert stored.block is not None and stored.block.resolution == "retry_stage"
    assert env.outbox_types() == ["BlockResolved"]


def test_resume_preserves_planning_backoff_gate(env_factory):
    env = env_factory()
    plan = Plan(
        project_id="project-1",
        id="p1",
        brief="b",
        phase=PlanPhase.ENRICHING,
        goals=[Goal(id="g1", name="g1", position=0, description="")],
    )
    plan.record_planning_retry(env.clock.now() + timedelta(seconds=300))
    plan.pause("operator pause")
    env.seed(plan)

    resume_plan("p1", env.uow)

    stored = env.stored("p1")
    assert stored.planning_retry_not_before is not None
    assert stored.planning_attempts == 1
    assert env.uow.plans.claim_one_unit("w1", lease_seconds=60) is None


def test_resume_retry_bypasses_should_retry_for_auth_error(env_factory):
    """AUTH_ERROR is non-retryable for the policy, but the HUMAN retry resets the
    budget: after resume the task runs again despite the terminal kind."""
    env = env_factory(
        {
            "t0": DummyBehavior(
                always_fail=True, fail_kind=FailureKind.AUTH_ERROR, fail_reason="key rejected"
            )
        }
    )
    env.seed(running_plan(retry_max=5))

    sig = asyncio.run(drive(env))
    assert sig == "paused"
    assert env.stored("p1").paused  # auto-paused on the non-retryable failure
    assert env.runner.calls["t0"] == 1

    env.runner.script["t0"] = DummyBehavior(output="ok")  # human fixed the key
    retry_task("p1", "g1", "t0", env.uow, env.clock)
    resume_plan("p1", env.uow)
    sig = asyncio.run(drive(env))

    assert env.runner.calls["t0"] == 2  # ran again with the fresh budget
    assert env.stored("p1").phase == PlanPhase.REVIEW  # succeeded and exhausted scan


# ---- the auto-pause (execution handler) ----
def test_auto_pause_on_exhaustion_keeps_goal_open(env_factory):
    env = env_factory({"t0": DummyBehavior(always_fail=True, fail_reason="boom")})
    env.seed(running_plan(retry_max=2))

    sig = asyncio.run(drive(env))

    assert sig == "paused"
    stored = env.stored("p1")
    assert stored.phase == PlanPhase.RUNNING and stored.paused
    assert stored.goals[0].status == Status.RUNNING  # goal NOT failed — recoverable
    assert stored.goals[0].tasks[0].status == Status.FAILED
    types = env.outbox_types()
    assert types.count("PlanPaused") == 1
    assert "GoalFailedEvent" not in types and "PlanFailed" not in types
    # the failure kind now travels on the coarse events
    assert "TaskFailedEvent" in types


def test_human_pause_survives_an_in_flight_terminal_failure(env_factory):
    """Race: a human pauses while an attempt is in flight; that attempt then
    exhausts its budget. The task is recorded FAILED, but the human pause keeps
    its auto=False semantics — exactly one PlanPaused (the human's), no flip to
    needs-attention."""
    from src.app.testing.fakes import DummyAgentRunner

    class PauseMidRun(DummyAgentRunner):
        """Pause the plan (as a human would) during the side effect, then fail
        terminally — so finalize runs against an already-paused plan."""

        def __init__(self, script, uow):
            super().__init__(script)
            self._uow = uow

        async def run(self, task, spec, **kw):
            pause_plan("p1", self._uow, "human pause mid-run")
            return await super().run(task, spec, **kw)

    env = env_factory()
    env.seed(running_plan(retry_max=1))  # one attempt, then terminal
    env.runner = PauseMidRun({"t0": DummyBehavior(always_fail=True, fail_reason="boom")}, env.uow)
    env.args = (env.uow, env.runner, env.agents, env.ws, env.sink, env.clock)

    sig = asyncio.run(advance_plan("p1", *env.args))

    assert sig == "paused"
    stored = env.stored("p1")
    assert stored.paused
    assert stored.goals[0].tasks[0].status == Status.FAILED  # failure still recorded
    types = env.outbox_types()
    # the human pause emitted exactly one PlanPaused; the terminal failure did NOT
    # emit a second (auto=True) one that would flip the semantics
    assert types.count("PlanPaused") == 1
    assert "TaskFailedEvent" in types


def test_pause_then_resume_completes_to_review(env_factory):
    """Full recovery walk: exhaust -> auto-pause -> human fixes the cause ->
    resume (fresh budget) -> the task succeeds -> RUNNING exhausts into REVIEW
    -> finish -> DONE."""
    env = env_factory({"t0": DummyBehavior(always_fail=True, fail_reason="rate limited")})
    env.seed(running_plan(retry_max=2))

    assert asyncio.run(drive(env)) == "paused"
    assert env.stored("p1").paused

    env.runner.script["t0"] = DummyBehavior(output="ok")  # quota restored
    retry_task("p1", "g1", "t0", env.uow, env.clock)
    resume_plan("p1", env.uow)
    assert asyncio.run(drive(env)) == "paused"  # the REVIEW gate this time
    assert env.stored("p1").phase == PlanPhase.REVIEW

    finish_review("p1", env.uow)
    final = env.stored("p1")
    assert final.phase == PlanPhase.DONE
    assert final.goals[0].tasks[0].status == Status.DONE
