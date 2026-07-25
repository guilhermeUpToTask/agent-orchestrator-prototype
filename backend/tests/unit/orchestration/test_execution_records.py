"""Stable execution run/attempt identity on both memory and SQLite UoWs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from src.app.execution_records import (
    ExecutionAttemptStatus,
    ExecutionRunStatus,
    RuntimeCircuit,
)
from src.app.handlers.execution_handler import ExecutionHandler
from src.app.provider_capacity import ProviderCapacityPolicy
from src.app.runtime_failures import LimitScope
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


def test_runtime_circuit_round_trips_limit_scope_and_probe_fields(env_factory):
    """The 0012 columns survive a write/read cycle identically on the in-memory
    fake and the bound SQLite adapter."""
    env = env_factory()
    opened = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    circuit = RuntimeCircuit(
        runtime="pi",
        provider_id="openrouter",
        model_id="nemotron",
        failure_count=3,
        opened_at=opened,
        retry_at=opened + timedelta(seconds=90),
        last_failure_kind="rate_limit",
        safe_message="Upstream error from Nvidia: ResourceExhausted",
        manual_intervention=False,
        limit_scope="request_concurrency",
        probe_holder="run-7",
        probe_started_at=opened + timedelta(seconds=30),
    )

    with env.uow:
        env.uow.executions.upsert_runtime_circuit(circuit)
    with env.uow:
        assert env.uow.executions.get_runtime_circuit("pi", "openrouter", "nemotron") == circuit


def test_provider_wide_circuit_is_addressed_by_none_not_a_sentinel(env_factory):
    """An account-level (quota) circuit is keyed with model_id=None. Callers only
    ever speak None; the SQLite adapter's storage sentinel must not leak out, and
    a provider-wide circuit must not collide with a per-model one."""
    env = env_factory()
    opened = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)

    def _circuit(model_id: str | None, count: int) -> RuntimeCircuit:
        return RuntimeCircuit(
            runtime="pi",
            provider_id="openrouter",
            model_id=model_id,
            failure_count=count,
            opened_at=opened,
            retry_at=opened + timedelta(seconds=60),
            last_failure_kind="rate_limit",
            safe_message="quota",
            limit_scope="daily_quota" if model_id is None else "request_concurrency",
        )

    with env.uow:
        env.uow.executions.upsert_runtime_circuit(_circuit(None, 1))
        env.uow.executions.upsert_runtime_circuit(_circuit("nemotron", 2))

    with env.uow:
        provider_wide = env.uow.executions.get_runtime_circuit("pi", "openrouter", None)
        per_model = env.uow.executions.get_runtime_circuit("pi", "openrouter", "nemotron")
    assert provider_wide is not None and provider_wide.model_id is None
    assert provider_wide.failure_count == 1
    assert per_model is not None and per_model.model_id == "nemotron"
    assert per_model.failure_count == 2

    # clearing one must not clear the other
    with env.uow:
        env.uow.executions.clear_runtime_circuit("pi", "openrouter", None)
    with env.uow:
        assert env.uow.executions.get_runtime_circuit("pi", "openrouter", None) is None
        assert env.uow.executions.get_runtime_circuit("pi", "openrouter", "nemotron") is not None


def _cyclic_plan(env, *, task_ids=("t1",), max_attempts=6, backoff=1) -> Plan:
    """A RUNNING plan with one active cycle and one goal — the shape a goal-lease
    worker drives via ExecutionHandler.handle_goal."""
    plan = _plan(max_attempts=max_attempts)
    plan.retry_policy = RetryPolicy(
        max_attempts=max_attempts,
        initial_backoff_seconds=backoff,
        max_backoff_seconds=backoff,
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
                        Task(
                            id=task_id,
                            name=task_id,
                            position=index,
                            description="",
                            agent_id="a1",
                        )
                        for index, task_id in enumerate(task_ids)
                    ],
                )
            ],
        )
    ]
    return plan


def test_request_concurrency_requeues_without_opening_a_circuit(env_factory):
    """A concurrency cap is not an outage: the provider served every other
    in-flight request and refused only the one over its ceiling. The remedy is
    'send fewer at once', so the task requeues on its own backoff and NO circuit
    opens -- a circuit would halt a provider that is working."""
    agent = make_agent_spec().model_copy(
        update={"runtime_type": "pi", "provider_id": "openrouter", "model_id": "nemotron"}
    )
    env = env_factory(
        {
            "t1": DummyBehavior(
                always_fail=True,
                fail_kind=FailureKind.RATE_LIMIT,
                fail_reason=(
                    "Upstream error from Nvidia: ResourceExhausted: "
                    "Worker local total request limit reached (33/32)"
                ),
                fail_limit_scope=LimitScope.REQUEST_CONCURRENCY,
            )
        },
        agents=[agent],
    )
    env.seed(_cyclic_plan(env))

    execution = ExecutionHandler(env.runner, env.agents, env.ws, env.sink, env.clock)

    def drive() -> str:
        return asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value

    # Several concurrency refusals in a row: each one requeues, none opens a
    # circuit, and the plan never blocks.
    for _ in range(4):
        assert drive() == "continue"
        env.clock.advance(5)

    stored = env.stored("p1")
    assert stored.block is None
    assert stored.goal_blocks == {}
    assert stored.status == PlanStatus.RUNNING
    assert stored.active_cycle is not None
    assert stored.active_cycle.goals[0].tasks[0].status == Status.PENDING
    assert env.runner.calls == {"t1": 4}
    with env.uow:
        assert env.uow.executions.get_runtime_circuit("pi", "openrouter", "nemotron") is None
        assert env.uow.executions.get_runtime_circuit("pi", "openrouter", None) is None


def test_capacity_storm_inside_the_ceiling_never_latches_or_blocks(env_factory):
    """The old rule compared a provider-global failure_count against a PER-TASK
    attempt budget and latched manual_intervention after a handful of failures
    shared across concurrent goals. Escalation is now duration-based: a storm of
    refusals inside the ceiling keeps waiting, and the plan stays RUNNING."""
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
    # max_attempts=2 and a RATE_LIMIT kind budget of 6 both used to terminate the
    # task long before this many failures.
    env.seed(_cyclic_plan(env, task_ids=("t1", "t2"), max_attempts=2))
    execution = ExecutionHandler(
        env.runner,
        env.agents,
        env.ws,
        env.sink,
        env.clock,
        capacity=ProviderCapacityPolicy(outage_ceiling_seconds=600),
    )

    def drive() -> str:
        return asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value

    for _ in range(10):
        assert drive() == "continue"
        env.clock.advance(5)

    stored = env.stored("p1")
    assert stored.block is None
    assert stored.goal_blocks == {}
    assert stored.status == PlanStatus.RUNNING
    assert stored.active_cycle is not None
    assert stored.active_cycle.goals[0].tasks[0].status == Status.PENDING
    # the later task never ran: a waiting head task still holds the goal
    assert stored.active_cycle.goals[0].tasks[1].status == Status.PENDING
    assert env.runner.calls == {"t1": 10}
    with env.uow:
        circuit = env.uow.executions.get_runtime_circuit("pi", "nvidia", "nemotron")
    assert circuit is not None and not circuit.manual_intervention
    assert circuit.failure_count == 10  # kept for telemetry, no longer a latch
    assert circuit.opened_at == env.clock.now() - timedelta(seconds=50)  # outage START


def test_connection_error_waits_on_a_circuit_instead_of_failing_the_task(env_factory):
    """An unreachable or overloaded endpoint is not a defect in this task. It used
    to exhaust the per-task budget and open a goal block for something no human
    edit could fix; it now waits on a circuit like any other capacity failure."""
    agent = make_agent_spec().model_copy(
        update={"runtime_type": "pi", "provider_id": "openrouter", "model_id": "nemotron"}
    )
    env = env_factory(
        {
            "t1": DummyBehavior(
                always_fail=True,
                fail_kind=FailureKind.CONNECTION_ERROR,
                fail_reason="connection reset by peer",
            )
        },
        agents=[agent],
    )
    # max_attempts=2 with a CONNECTION_ERROR kind budget of 5: the old path
    # terminated this task well inside the loop below.
    env.seed(_cyclic_plan(env, max_attempts=2))
    execution = ExecutionHandler(
        env.runner,
        env.agents,
        env.ws,
        env.sink,
        env.clock,
        capacity=ProviderCapacityPolicy(outage_ceiling_seconds=600),
    )

    def drive() -> str:
        return asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value

    for _ in range(8):
        assert drive() == "continue"
        env.clock.advance(5)

    stored = env.stored("p1")
    assert stored.goal_blocks == {}
    assert stored.status == PlanStatus.RUNNING
    assert stored.active_cycle is not None
    assert stored.active_cycle.goals[0].tasks[0].status == Status.PENDING
    with env.uow:
        circuit = env.uow.executions.get_runtime_circuit("pi", "openrouter", "nemotron")
    assert circuit is not None and not circuit.manual_intervention
    assert circuit.last_failure_kind == "connection_error"

    # still bounded: past the ceiling it escalates like any capacity outage
    env.clock.advance(1_000)
    assert drive() == "paused"
    block = env.stored("p1").goal_blocks.get("g1")
    assert block is not None and block.kind == "provider_capacity"


def test_outage_past_the_ceiling_latches_and_opens_a_goal_block(env_factory):
    """The backstop: a 'transient' signature that never resolves (a revoked key
    returning 429, a wrong base_url) must eventually reach a human instead of
    waiting forever while the root still reports RUNNING."""
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
    env.seed(_cyclic_plan(env, task_ids=("t1", "t2")))
    execution = ExecutionHandler(
        env.runner,
        env.agents,
        env.ws,
        env.sink,
        env.clock,
        capacity=ProviderCapacityPolicy(outage_ceiling_seconds=100),
    )

    def drive() -> str:
        return asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value

    assert drive() == "continue"  # opens the circuit
    env.clock.advance(500)  # outage now far older than the ceiling
    assert drive() == "paused"

    stored = env.stored("p1")
    assert stored.block is None  # per-goal block, not the legacy scalar
    block = stored.goal_blocks.get("g1")
    assert block is not None and block.active and block.kind == "provider_capacity"
    assert block.legal_resolutions == ["wait_and_retry", "edit_task", "start_replan"]
    assert stored.active_cycle is not None
    assert stored.active_cycle.goals[0].tasks[1].status == Status.PENDING
    with env.uow:
        circuit = env.uow.executions.get_runtime_circuit("pi", "nvidia", "nemotron")
    assert circuit is not None and circuit.manual_intervention


def test_daily_quota_waits_past_the_ordinary_ceiling(env_factory):
    """A free-tier daily allowance can legitimately take a full day to reset, so
    it must not escalate on the ordinary ceiling -- that would block precisely the
    case this design exists to survive."""
    agent = make_agent_spec().model_copy(
        update={"runtime_type": "pi", "provider_id": "openrouter", "model_id": "nemotron"}
    )
    env = env_factory(
        {
            "t1": DummyBehavior(
                always_fail=True,
                fail_kind=FailureKind.RATE_LIMIT,
                fail_reason="free-models-per-day limit reached",
                fail_limit_scope=LimitScope.DAILY_QUOTA,
            )
        },
        agents=[agent],
    )
    env.seed(_cyclic_plan(env))
    execution = ExecutionHandler(
        env.runner,
        env.agents,
        env.ws,
        env.sink,
        env.clock,
        capacity=ProviderCapacityPolicy(
            outage_ceiling_seconds=100,
            daily_quota_ceiling_seconds=10_000,
        ),
    )

    def drive() -> str:
        return asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value

    assert drive() == "continue"

    # A daily quota also carries a 1h minimum backoff, so the plan is still
    # WAITING here -- past the ordinary ceiling, but not yet due to probe.
    env.clock.advance(500)
    assert drive() == "not_ready"
    assert env.stored("p1").goal_blocks == {}

    # Now past the probe window and still inside the daily ceiling: it retries
    # rather than escalating, even though the ordinary ceiling is long gone.
    env.clock.advance(4_000)
    assert drive() == "continue"
    assert env.stored("p1").goal_blocks == {}

    with env.uow:
        # an account-level limit keys provider-wide: routing to another model
        # cannot escape it
        circuit = env.uow.executions.get_runtime_circuit("pi", "openrouter", None)
    assert circuit is not None and not circuit.manual_intervention
    assert circuit.limit_scope == "daily_quota"

    env.clock.advance(20_000)  # past the daily ceiling too, and past the probe window
    assert drive() == "paused"
    block = env.stored("p1").goal_blocks.get("g1")
    assert block is not None and block.kind == "provider_capacity"
