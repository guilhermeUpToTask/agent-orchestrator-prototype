"""Stable execution run/attempt identity on both memory and SQLite UoWs."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from src.app.execution_records import ExecutionAttemptStatus, ExecutionRunStatus
from src.app.handlers.execution_handler import ExecutionHandler
from src.app.testing.fakes import DummyBehavior
from src.app.use_cases.advance_plan import advance_plan
from src.app.use_cases.pause_resume import resume_plan, retry_task
from src.app.use_cases.reconcile_runtime import reconcile_stale_attempts
from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import Cycle, CycleStatus, PlanStatus
from src.domain.entities.task import Task
from src.domain.policies.retry_policies import RetryPolicy
from src.domain.value_objects.lifecycle import FailureKind, Status
from tests.support import make_agent_spec


def _plan(*, max_attempts: int = 3) -> Plan:
    return Plan(
        project_id="project-1",
        id="p1",
        brief="b",
        phase=PlanPhase.RUNNING,
        retry_policy=RetryPolicy(
            max_attempts=max_attempts,
            initial_backoff_seconds=0,
        ),
        goals=[
            Goal(
                id="g1",
                name="g1",
                position=0,
                description="",
                tasks=[
                    Task(
                        id="t1",
                        name="t1",
                        position=0,
                        description="",
                        agent_id="a1",
                    )
                ],
            )
        ],
    )


def _identity(key: str) -> tuple[str, str]:
    plan_id, goal_id, task_id, run_id, attempt_number, attempt_id = key.split(":")
    assert (plan_id, goal_id, task_id) == ("p1", "g1", "t1")
    assert int(attempt_number) > 0
    UUID(run_id)
    UUID(attempt_id)
    return run_id, attempt_id


def _records(env, key: str):
    run_id, attempt_id = _identity(key)
    with env.uow:
        return (
            env.uow.executions.get_run(run_id),
            env.uow.executions.get_attempt(attempt_id),
        )


def test_success_persists_stable_identity_before_runtime(env_factory):
    env = env_factory()
    env.seed(_plan())

    assert asyncio.run(advance_plan("p1", *env.args)) == "continue"

    assert len(env.runner.idempotency_keys) == 1
    run, attempt = _records(env, env.runner.idempotency_keys[0])
    assert run.status == ExecutionRunStatus.SUCCEEDED
    assert attempt.status == ExecutionAttemptStatus.SUCCEEDED
    assert attempt.run_id == run.id
    assert attempt.number == 1
    assert attempt.task_attempt == 1
    assert attempt.started_at == env.clock.now()
    assert attempt.completed_at == env.clock.now()
    assert env.ws.begun == [("p1", "t1", 1)]


def test_automatic_retry_reuses_run_and_gets_new_attempt(env_factory):
    env = env_factory({"t1": DummyBehavior(fail_times=1)})
    env.seed(_plan())

    assert asyncio.run(advance_plan("p1", *env.args)) == "continue"
    first_run, first_attempt = _records(env, env.runner.idempotency_keys[0])
    assert first_run.status == ExecutionRunStatus.RETRYING
    assert first_attempt.status == ExecutionAttemptStatus.FAILED

    assert asyncio.run(advance_plan("p1", *env.args)) == "continue"
    second_run, second_attempt = _records(env, env.runner.idempotency_keys[1])

    assert second_run.id == first_run.id
    assert second_run.status == ExecutionRunStatus.SUCCEEDED
    assert second_attempt.id != first_attempt.id
    assert [first_attempt.number, second_attempt.number] == [1, 2]
    assert [first_attempt.task_attempt, second_attempt.task_attempt] == [1, 2]
    assert env.ws.begun == [("p1", "t1", 1), ("p1", "t1", 2)]


def test_human_retry_starts_new_run_without_reusing_attempt_number(env_factory):
    env = env_factory(
        {
            "t1": DummyBehavior(
                always_fail=True,
                fail_reason="bad credentials",
                fail_kind=FailureKind.AUTH_ERROR,
            )
        }
    )
    env.seed(_plan())

    assert asyncio.run(advance_plan("p1", *env.args)) == "paused"
    failed_run, failed_attempt = _records(env, env.runner.idempotency_keys[0])
    assert failed_run.status == ExecutionRunStatus.FAILED

    env.runner.script["t1"] = DummyBehavior(output="recovered")
    retry_task("p1", "g1", "t1", env.uow, env.clock)
    resume_plan("p1", env.uow)
    assert asyncio.run(advance_plan("p1", *env.args)) == "continue"
    resumed_run, resumed_attempt = _records(env, env.runner.idempotency_keys[1])

    assert resumed_run.id != failed_run.id
    assert resumed_run.status == ExecutionRunStatus.SUCCEEDED
    assert resumed_attempt.number == 2
    assert resumed_attempt.task_attempt == 1  # domain retry budget reset is separate
    assert failed_attempt.number == 1
    assert env.ws.begun == [("p1", "t1", 1), ("p1", "t1", 2)]


def test_unexpected_runtime_crash_leaves_discoverable_open_attempt(env_factory, monkeypatch):
    env = env_factory()
    env.seed(_plan())

    async def crash(*args, **kwargs):
        raise RuntimeError("worker died")

    monkeypatch.setattr(env.runner, "run", crash)
    with pytest.raises(RuntimeError, match="worker died"):
        asyncio.run(advance_plan("p1", *env.args))

    with env.uow:
        open_attempts = env.uow.executions.list_open_attempts("p1")
        run = env.uow.executions.get_run(open_attempts[0].run_id)
    assert len(open_attempts) == 1
    assert open_attempts[0].status == ExecutionAttemptStatus.RUNNING
    assert run.status == ExecutionRunStatus.RUNNING
    assert env.stored("p1").goals[0].tasks[0].status == Status.RUNNING
    assert "TaskStarted" in env.outbox_types()


def test_startup_reconciliation_respects_live_lease_then_abandons_stale_attempt(
    env_factory, monkeypatch
):
    env = env_factory()
    env.seed(_plan())

    async def crash(*args, **kwargs):
        raise RuntimeError("worker died")

    monkeypatch.setattr(env.runner, "run", crash)
    with pytest.raises(RuntimeError, match="worker died"):
        asyncio.run(advance_plan("p1", *env.args))

    claimed = env.uow.plans.claim_one_unit("live-worker", lease_seconds=60)
    assert claimed is not None and claimed.id == "p1"
    assert reconcile_stale_attempts(env.uow, env.clock) == []

    env.uow.plans.release("p1", "live-worker")
    reconciled = reconcile_stale_attempts(env.uow, env.clock)
    assert len(reconciled) == 1
    with env.uow:
        attempt = env.uow.executions.get_attempt(reconciled[0])
        run = env.uow.executions.get_run(attempt.run_id)
    assert attempt.status == ExecutionAttemptStatus.ABANDONED
    assert run.status == ExecutionRunStatus.ABANDONED
    assert env.stored("p1").goals[0].tasks[0].status == Status.RUNNING


def test_attempt_creation_rolls_back_with_task_start_and_outbox(env_factory, monkeypatch):
    env = env_factory()
    env.seed(_plan())
    original_add = env.uow.executions.add_attempt

    def fail_after_add(attempt):
        original_add(attempt)
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(env.uow.executions, "add_attempt", fail_after_add)
    with pytest.raises(RuntimeError, match="injected transaction failure"):
        asyncio.run(advance_plan("p1", *env.args))

    with env.uow:
        assert env.uow.executions.list_open_attempts("p1") == []
    assert env.stored("p1").goals[0].tasks[0].status == Status.PENDING
    assert "TaskStarted" not in env.outbox_types()


def test_provider_circuit_blocks_head_goal_without_running_later_task(env_factory):
    """Domain unfreeze #13 (symmetric per-goal leases): the plan-level tick no
    longer dispatches cyclic execution at all, so this drives the single goal
    directly via ExecutionHandler.handle_goal — the same entry point a
    goal-lease worker (claim_ready_goal / drive_goal) uses in production."""
    agent = make_agent_spec().model_copy(
        update={"runtime_type": "pi", "provider_id": "nvidia", "model_id": "nemotron"}
    )
    env = env_factory(
        {
            "t1": DummyBehavior(
                always_fail=True,
                fail_kind=FailureKind.RATE_LIMIT,
                fail_reason="NVIDIA ResourceExhausted",
            )
        },
        agents=[agent],
    )
    plan = _plan(max_attempts=2)
    plan.retry_policy = RetryPolicy(
        max_attempts=2,
        initial_backoff_seconds=1,
        max_backoff_seconds=1,
        jitter_ratio=0,
    )
    plan.status = PlanStatus.RUNNING
    plan.cycles = [
        Cycle(
            id="cycle-1",
            intent_proposal_id="intent-1",
            draft_id="draft-1",
            status=CycleStatus.ACTIVE,
            started_at=env.clock.now(),
            goals=[
                Goal(
                    id="g1",
                    name="g1",
                    position=0,
                    description="",
                    tasks=[
                        Task(id="t1", name="t1", position=0, description="", agent_id="a1"),
                        Task(id="t2", name="t2", position=1, description="", agent_id="a1"),
                    ],
                )
            ],
        )
    ]
    env.seed(plan)

    execution = ExecutionHandler(env.runner, env.agents, env.ws, env.sink, env.clock)

    def drive() -> str:
        stored = env.stored("p1")
        return asyncio.run(execution.handle_goal("p1", "g1", stored, env.uow)).value

    assert drive() == "continue"
    assert drive() == "not_ready"
    assert env.runner.calls == {"t1": 1}

    env.clock.advance(5)
    assert drive() == "continue"
    env.clock.advance(5)
    assert drive() == "continue"
    stored = env.stored("p1")
    assert stored.block is None
    assert stored.goal_blocks == {}
    assert stored.active_cycle is not None
    assert stored.active_cycle.goals[0].tasks[0].status == Status.PENDING
    assert env.runner.calls == {"t1": 3}
    with env.uow:
        circuit = env.uow.executions.get_runtime_circuit("pi", "nvidia", "nemotron")
    assert circuit is not None and not circuit.manual_intervention

    for _ in range(2):
        env.clock.advance(5)
        assert drive() == "continue"

    env.clock.advance(5)
    assert drive() == "paused"
    stored = env.stored("p1")
    assert stored.block is None  # per-goal block, not the legacy scalar
    block = stored.goal_blocks.get("g1")
    assert block is not None and block.active and block.kind == "provider_capacity"
    assert block.legal_resolutions == [
        "wait_and_retry",
        "edit_task",
        "start_replan",
    ]
    assert stored.active_cycle is not None
    assert stored.active_cycle.goals[0].tasks[1].status == Status.PENDING
    assert env.runner.calls == {"t1": 6}
    with env.uow:
        circuit = env.uow.executions.get_runtime_circuit("pi", "nvidia", "nemotron")
    assert circuit is not None and circuit.manual_intervention
