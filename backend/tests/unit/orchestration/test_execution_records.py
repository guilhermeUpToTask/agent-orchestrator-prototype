"""Stable execution run/attempt identity on both memory and SQLite UoWs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from agent_orchestrator.app.execution_records import (
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionRun,
    ExecutionRunStatus,
    RuntimeCircuit,
)
from agent_orchestrator.app.handlers.execution_handler import ExecutionHandler
from agent_orchestrator.app.provider_capacity import ProviderCapacityPolicy
from agent_orchestrator.app.runtime_failures import LimitScope
from agent_orchestrator.app.testing.fakes import DummyBehavior
from agent_orchestrator.app.use_cases.advance_plan import advance_plan
from agent_orchestrator.app.use_cases.operator_commands import resume_plan, retry_task
from agent_orchestrator.app.use_cases.reconcile_runtime import reconcile_stale_attempts
from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from agent_orchestrator.domain.entities.goal import Goal
from agent_orchestrator.domain.entities.planning_artifacts import Cycle, CycleStatus, PlanStatus
from agent_orchestrator.domain.entities.task import Task
from agent_orchestrator.domain.policies.retry_policies import RetryPolicy
from agent_orchestrator.domain.value_objects.lifecycle import FailureKind, Status
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


def test_startup_reconciliation_respects_a_live_GOAL_lease(env_factory, monkeypatch):
    """Reconciliation must not abandon an attempt another worker is still running.

    It gates on `plans.is_claim_live` — the PLAN claim. But since goal leases
    (ADR-001 / un-freeze #13) attempts are created by goal workers, and a goal
    worker does not hold the plan claim while it runs. So a second worker's
    STARTUP reconciliation sees a RUNNING attempt with no live plan claim and
    abandons a ledger row whose process is alive and about to finalize it.

    Single-worker restart is unaffected (the old process really is dead), which
    is why this survived: it needs two workers to show up at all."""
    env = env_factory()
    env.seed(_plan())

    async def crash(*args, **kwargs):
        raise RuntimeError("worker died")

    monkeypatch.setattr(env.runner, "run", crash)
    with pytest.raises(RuntimeError, match="worker died"):
        asyncio.run(advance_plan("p1", *env.args))

    # No plan claim — a goal worker holds the GOAL lease instead, and is alive.
    with env.uow:
        assert not env.uow.plans.is_claim_live("p1")
        goal_id = env.uow.executions.list_open_attempts("p1")[0].goal_id
    assert env.uow.goal_leases.claim_one_ready_goal(
        "p1", goal_id, "live-goal-worker", 300, env.clock.now()
    )

    assert reconcile_stale_attempts(env.uow, env.clock) == [], (
        "reconciliation abandoned an attempt whose goal worker is still alive"
    )


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


def _drive_one_capacity_refusal(env_factory, scope: LimitScope, *, backoff: int = 30):
    """One rate-limited attempt at `scope`; returns the armed retry gate."""
    agent = make_agent_spec().model_copy(
        update={"runtime_type": "pi", "provider_id": "openrouter", "model_id": "nemotron"}
    )
    env = env_factory(
        {
            "t1": DummyBehavior(
                always_fail=True,
                fail_kind=FailureKind.RATE_LIMIT,
                fail_reason="rate limited",
                fail_limit_scope=scope,
            )
        },
        agents=[agent],
    )
    env.seed(_cyclic_plan(env, backoff=backoff))
    execution = ExecutionHandler(env.runner, env.agents, env.ws, env.sink, env.clock)
    assert asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value == (
        "continue"
    )
    stored = env.stored("p1")
    assert stored.active_cycle is not None
    task = stored.active_cycle.goals[0].tasks[0]
    assert task.retry_not_before is not None
    return (task.retry_not_before - env.clock.now()).total_seconds()


def test_a_concurrency_refusal_waits_the_ordinary_curve_not_the_patient_one(env_factory):
    """Found by the Phase 1 series: `kind_backoff_scale` applied 4.0 to every
    rate-limited attempt regardless of limit_scope, so a concurrency refusal
    escalated on the same curve as an exhausted account quota. One Tier 1 run
    spent 37 minutes in backoff against an endpoint that answered concurrent
    probes instantly between attempts."""
    assert _drive_one_capacity_refusal(env_factory, LimitScope.REQUEST_CONCURRENCY) == 30.0


def test_an_account_quota_keeps_the_patient_curve(env_factory):
    """The other half of the same rule: an exhausted allowance is not waited out
    by retrying in 30 seconds, so the 4x scale must survive this change."""
    assert _drive_one_capacity_refusal(env_factory, LimitScope.QUOTA) == 120.0


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


def _two_goal_cycle(env) -> Plan:
    """Two INDEPENDENT goals (no depends_on) in one active cycle, both bound to the
    same provider/model — the shape that produced the probe herd, since each goal
    gets its own lease and its own UnitOfWork."""
    plan = _plan(max_attempts=6)
    plan.retry_policy = RetryPolicy(
        max_attempts=6, initial_backoff_seconds=10, max_backoff_seconds=10, jitter_ratio=0
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
                    id=goal_id,
                    name=goal_id,
                    position=position,
                    description="",
                    tasks=[
                        Task(
                            id=f"{goal_id}t0",
                            name="t",
                            position=0,
                            description="",
                            agent_id="a1",
                        )
                    ],
                )
                for position, goal_id in enumerate(("g1", "g2"))
            ],
        )
    ]
    return plan


def test_inflight_count_is_cross_plan_and_scoped_to_the_model(env_factory):
    """The gate counts against the UPSTREAM POOL, which every plan shares. A
    plan-scoped count would let two plans each open a full cap and blow straight
    through the provider's ceiling. Counted from attempts because ExecutionRun
    carries no provider binding."""
    env = env_factory()
    started = env.clock.now()
    # execution_runs.plan_id is a real FK, so every plan referenced must exist.
    for plan_id in ("p1", "p2"):
        plan = _plan()
        plan.id = plan_id
        plan.project_id = f"project-{plan_id}"
        env.seed(plan)

    def _attempt(attempt_id: str, plan_id: str, model_id: str, status):
        return ExecutionAttempt(
            id=attempt_id,
            run_id=f"run-{attempt_id}",
            plan_id=plan_id,
            goal_id="g1",
            # attempts are unique on (plan, goal, task, number)
            task_id=f"t-{attempt_id}",
            number=1,
            task_attempt=1,
            status=status,
            started_at=started,
            runtime="pi",
            provider_id="openrouter",
            model_id=model_id,
        )

    with env.uow:
        for attempt_id, plan_id, model_id, status in [
            ("a1", "p1", "nemotron", ExecutionAttemptStatus.RUNNING),
            ("a2", "p2", "nemotron", ExecutionAttemptStatus.RUNNING),  # a DIFFERENT plan
            ("a3", "p1", "other-model", ExecutionAttemptStatus.RUNNING),
            ("a4", "p1", "nemotron", ExecutionAttemptStatus.SUCCEEDED),  # finished
        ]:
            env.uow.executions.add_run(
                ExecutionRun(
                    id=f"run-{attempt_id}",
                    plan_id=plan_id,
                    goal_id="g1",
                    task_id=f"t-{attempt_id}",
                    status=ExecutionRunStatus.RUNNING,
                    started_at=started,
                )
            )
            env.uow.executions.add_attempt(_attempt(attempt_id, plan_id, model_id, status))
            if status != ExecutionAttemptStatus.RUNNING:
                env.uow.executions.finalize_attempt(
                    attempt_id,
                    attempt_status=status,
                    run_status=ExecutionRunStatus.SUCCEEDED,
                    completed_at=started,
                )

    with env.uow:
        # per-model: both plans' running nemotron attempts, not the other model,
        # not the finished one
        assert env.uow.executions.count_inflight_attempts("pi", "openrouter", "nemotron") == 2
        # provider-wide (an endpoint sharing one pool): every running model
        assert env.uow.executions.count_inflight_attempts("pi", "openrouter", None) == 3
        assert env.uow.executions.count_inflight_attempts("pi", "other-provider", None) == 0


def test_admission_gate_refuses_to_start_past_the_inflight_cap(env_factory):
    """Never fire the request that would be refused. The provider's ceiling was
    always there; it was just unmeasured, so the orchestrator discovered it by
    being rejected."""
    agent = make_agent_spec().model_copy(
        update={"runtime_type": "pi", "provider_id": "openrouter", "model_id": "nemotron"}
    )
    env = env_factory({"g1t0": DummyBehavior(output="ok")}, agents=[agent])
    env.seed(_two_goal_cycle(env))
    started = env.clock.now()
    # A DIFFERENT plan holds the in-flight attempt: the pool is shared, so the
    # gate must see it. execution_runs.plan_id is a real FK, hence a real plan.
    other = _plan()
    other.id = "p-other-plan"
    other.project_id = "project-other"
    env.seed(other)

    # one attempt already in flight against this provider/model
    with env.uow:
        env.uow.executions.add_run(
            ExecutionRun(
                id="run-x",
                plan_id="p-other-plan",
                goal_id="g9",
                task_id="t9",
                status=ExecutionRunStatus.RUNNING,
                started_at=started,
            )
        )
        env.uow.executions.add_attempt(
            ExecutionAttempt(
                id="att-x",
                run_id="run-x",
                plan_id="p-other-plan",
                goal_id="g9",
                task_id="t9",
                number=1,
                task_attempt=1,
                status=ExecutionAttemptStatus.RUNNING,
                started_at=started,
                runtime="pi",
                provider_id="openrouter",
                model_id="nemotron",
            )
        )

    at_cap = ExecutionHandler(
        env.runner,
        env.agents,
        env.ws,
        env.sink,
        env.clock,
        capacity=ProviderCapacityPolicy(max_inflight=1),
    )
    assert (
        asyncio.run(at_cap.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value == "not_ready"
    )
    assert env.runner.calls == {}  # never fired

    # raise the cap and the same work proceeds
    with_room = ExecutionHandler(
        env.runner,
        env.agents,
        env.ws,
        env.sink,
        env.clock,
        capacity=ProviderCapacityPolicy(max_inflight=2),
    )
    assert (
        asyncio.run(with_room.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value
        == "continue"
    )
    assert env.runner.calls == {"g1t0": 1}


def _routing_agents():
    """Two interchangeable agents on DIFFERENT providers, tiered. The bound one is
    the smart tier; the fallback is cheap."""
    smart = make_agent_spec().model_copy(
        update={
            "id": "a1",
            "model_role": "smart",
            "runtime_type": "pi",
            "provider_id": "openrouter",
            "model_id": "nemotron",
        }
    )
    cheap = make_agent_spec().model_copy(
        update={
            "id": "a2",
            "model_role": "cheap",
            "runtime_type": "pi",
            "provider_id": "local",
            "model_id": "qwen",
        }
    )
    return smart, cheap


def _open_circuit(env, provider_id, model_id, *, retry_in):
    with env.uow:
        env.uow.executions.upsert_runtime_circuit(
            RuntimeCircuit(
                runtime="pi",
                provider_id=provider_id,
                model_id=model_id,
                failure_count=1,
                opened_at=env.clock.now(),
                retry_at=env.clock.now() + timedelta(seconds=retry_in),
                last_failure_kind="rate_limit",
                safe_message="ResourceExhausted",
            )
        )


def test_selection_keeps_the_bound_agent_when_its_provider_is_free(env_factory):
    """Preference is primary. With nothing throttled, routing must not fire at all."""
    smart, cheap = _routing_agents()
    env = env_factory(
        {"g1t0": DummyBehavior(output="ok")}, agents=[smart, cheap], default_agent_id="a1"
    )
    env.seed(_two_goal_cycle(env))

    execution = ExecutionHandler(env.runner, env.agents, env.ws, env.sink, env.clock)
    assert asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value == (
        "continue"
    )

    with env.uow:
        attempts = env.uow.executions.list_attempts("p1")
    assert [a.model_id for a in attempts] == ["nemotron"]


def test_selection_waits_out_a_short_throttle_but_reroutes_a_long_one(env_factory):
    """On a paid setup, substituting a weaker model to dodge a ten-second 429 costs
    output quality for nothing -- so a short wait is waited out. A long outage is
    worth routing around, which is what makes free-tier parallelism work."""
    smart, cheap = _routing_agents()

    # short throttle -> stay on the preferred model and wait
    env = env_factory(
        {"g1t0": DummyBehavior(output="ok")}, agents=[smart, cheap], default_agent_id="a1"
    )
    env.seed(_two_goal_cycle(env))
    _open_circuit(env, "openrouter", "nemotron", retry_in=10)
    execution = ExecutionHandler(env.runner, env.agents, env.ws, env.sink, env.clock)
    assert asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value == (
        "not_ready"
    )
    assert env.runner.calls == {}

    # long outage -> route to the cheap tier and keep making progress
    env2 = env_factory(
        {"g1t0": DummyBehavior(output="ok")}, agents=[smart, cheap], default_agent_id="a1"
    )
    env2.seed(_two_goal_cycle(env2))
    _open_circuit(env2, "openrouter", "nemotron", retry_in=3_600)
    execution2 = ExecutionHandler(env2.runner, env2.agents, env2.ws, env2.sink, env2.clock)
    assert asyncio.run(execution2.handle_goal("p1", "g1", env2.stored("p1"), env2.uow)).value == (
        "continue"
    )
    with env2.uow:
        attempts = env2.uow.executions.list_attempts("p1")
    assert [a.model_id for a in attempts] == ["qwen"]


def test_selection_reroutes_immediately_when_the_preferred_pool_is_full(env_factory):
    """An at-capacity provider has no wait to compare against -- another pool may be
    idle right now. Applying the time threshold here would make the admission gate
    serialize exactly the work it exists to parallelize."""
    smart, cheap = _routing_agents()
    env = env_factory(
        {"g1t0": DummyBehavior(output="ok")}, agents=[smart, cheap], default_agent_id="a1"
    )
    env.seed(_two_goal_cycle(env))
    started = env.clock.now()
    other = _plan()
    other.id = "p-busy"
    other.project_id = "project-busy"
    env.seed(other)
    with env.uow:
        env.uow.executions.add_run(
            ExecutionRun(
                id="run-b",
                plan_id="p-busy",
                goal_id="g9",
                task_id="t9",
                status=ExecutionRunStatus.RUNNING,
                started_at=started,
            )
        )
        env.uow.executions.add_attempt(
            ExecutionAttempt(
                id="att-b",
                run_id="run-b",
                plan_id="p-busy",
                goal_id="g9",
                task_id="t9",
                number=1,
                task_attempt=1,
                status=ExecutionAttemptStatus.RUNNING,
                started_at=started,
                runtime="pi",
                provider_id="openrouter",
                model_id="nemotron",
            )
        )

    execution = ExecutionHandler(
        env.runner,
        env.agents,
        env.ws,
        env.sink,
        env.clock,
        capacity=ProviderCapacityPolicy(max_inflight=1),
    )
    assert asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value == (
        "continue"
    )
    with env.uow:
        attempts = [a for a in env.uow.executions.list_attempts("p1")]
    assert [a.model_id for a in attempts] == ["qwen"]  # rerouted, not delayed


def test_selection_never_mutates_the_persisted_binding(env_factory):
    """Substitution is a RUNTIME choice. The task's agent_id records the operator's
    preference and belongs to the aggregate; only the attempt records what ran."""
    smart, cheap = _routing_agents()
    env = env_factory(
        {"g1t0": DummyBehavior(output="ok")}, agents=[smart, cheap], default_agent_id="a1"
    )
    env.seed(_two_goal_cycle(env))
    _open_circuit(env, "openrouter", "nemotron", retry_in=3_600)

    execution = ExecutionHandler(env.runner, env.agents, env.ws, env.sink, env.clock)
    asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow))

    stored = env.stored("p1")
    assert stored.active_cycle is not None
    assert stored.active_cycle.goals[0].tasks[0].agent_id == "a1"  # binding untouched
    with env.uow:
        attempts = env.uow.executions.list_attempts("p1")
    assert [a.provider_id for a in attempts] == ["local"]  # but 'local' actually ran


def test_selection_waits_when_every_capable_agent_is_throttled(env_factory):
    """With nowhere better to go, it falls back to the preferred spec and takes the
    ordinary wait -- never a block merely because routing found no alternative."""
    smart, cheap = _routing_agents()
    env = env_factory(
        {"g1t0": DummyBehavior(output="ok")}, agents=[smart, cheap], default_agent_id="a1"
    )
    env.seed(_two_goal_cycle(env))
    _open_circuit(env, "openrouter", "nemotron", retry_in=3_600)
    _open_circuit(env, "local", "qwen", retry_in=3_600)

    execution = ExecutionHandler(env.runner, env.agents, env.ws, env.sink, env.clock)
    assert asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value == (
        "not_ready"
    )
    assert env.runner.calls == {}
    stored = env.stored("p1")
    assert stored.goal_blocks == {}
    assert stored.block is None


def test_probe_claim_admits_exactly_one_holder_per_window(env_factory):
    """The atomicity contract behind the half-open probe, on BOTH backends. The
    claim must be a single conditional write: a read-then-write would let two
    concurrent runners each observe the probe free and both probe, which is the
    herd that made one outage window cost four failures with four goals in flight.
    """
    env = env_factory()
    now = env.clock.now()
    with env.uow:
        env.uow.executions.upsert_runtime_circuit(
            RuntimeCircuit(
                runtime="pi",
                provider_id="openrouter",
                model_id="nemotron",
                failure_count=3,
                opened_at=now - timedelta(seconds=30),
                retry_at=now - timedelta(seconds=1),
                last_failure_kind="rate_limit",
                safe_message="ResourceExhausted",
            )
        )

    stale_before = now - timedelta(seconds=100)
    with env.uow:
        first = env.uow.executions.try_claim_circuit_probe(
            "pi", "openrouter", "nemotron", holder="runner-a", now=now, stale_before=stale_before
        )
        second = env.uow.executions.try_claim_circuit_probe(
            "pi", "openrouter", "nemotron", holder="runner-b", now=now, stale_before=stale_before
        )
    assert (first, second) == (True, False)

    with env.uow:
        circuit = env.uow.executions.get_runtime_circuit("pi", "openrouter", "nemotron")
    assert circuit is not None
    assert circuit.probe_holder == "runner-a"  # the loser must not overwrite it
    assert circuit.probe_started_at == now

    # released -> claimable again in the next window
    with env.uow:
        env.uow.executions.release_circuit_probe("pi", "openrouter", "nemotron")
    with env.uow:
        assert env.uow.executions.try_claim_circuit_probe(
            "pi", "openrouter", "nemotron", holder="runner-c", now=now, stale_before=stale_before
        )


def test_probe_claim_on_a_missing_circuit_is_refused(env_factory):
    """No circuit means nothing to probe; a claim must not create a row."""
    env = env_factory()
    now = env.clock.now()
    with env.uow:
        assert not env.uow.executions.try_claim_circuit_probe(
            "pi",
            "openrouter",
            "nemotron",
            holder="runner-a",
            now=now,
            stale_before=now - timedelta(seconds=100),
        )
    with env.uow:
        assert env.uow.executions.get_runtime_circuit("pi", "openrouter", "nemotron") is None


def test_a_stale_probe_is_reclaimed_after_its_holder_dies(env_factory):
    """A probe whose holder crashed must not gate the provider forever."""
    agent = make_agent_spec().model_copy(
        update={"runtime_type": "pi", "provider_id": "openrouter", "model_id": "nemotron"}
    )
    env = env_factory({"g1t0": DummyBehavior(output="ok")}, agents=[agent])
    env.seed(_two_goal_cycle(env))

    with env.uow:
        env.uow.executions.upsert_runtime_circuit(
            RuntimeCircuit(
                runtime="pi",
                provider_id="openrouter",
                model_id="nemotron",
                failure_count=1,
                opened_at=env.clock.now() - timedelta(seconds=60),
                retry_at=env.clock.now() - timedelta(seconds=1),
                last_failure_kind="rate_limit",
                safe_message="ResourceExhausted",
                probe_holder="worker-that-died",
                probe_started_at=env.clock.now() - timedelta(seconds=30),
            )
        )

    execution = ExecutionHandler(
        env.runner,
        env.agents,
        env.ws,
        env.sink,
        env.clock,
        capacity=ProviderCapacityPolicy(probe_stale_after_seconds=100),
    )

    def drive() -> str:
        return asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value

    # held for 30s, cutoff 100s -> still someone else's probe
    assert drive() == "not_ready"
    assert env.runner.calls == {}

    # past the cutoff the abandoned probe is reclaimable
    env.clock.advance(200)
    assert drive() == "continue"
    assert env.runner.calls == {"g1t0": 1}


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

    # Past the daily ceiling (10_000s from the outage start) while still retrying
    # normally. Deliberately NOT a single huge jump: silence for longer than a whole
    # daily ceiling is treated as a DIFFERENT outage, so an unrealistically quiet
    # clock would reset the window rather than escalate.
    env.clock.advance(7_500)
    assert drive() == "paused"
    block = env.stored("p1").goal_blocks.get("g1")
    assert block is not None and block.kind == "provider_capacity"


class _OneProviderCatalog:
    """The narrowest `ModelProviderRepository` the admission gate needs."""

    def __init__(self, provider):
        self._provider = provider

    def get(self, provider_id: str):
        return self._provider

    def list(self):
        return [self._provider]


def test_an_unusable_stored_cap_does_not_wedge_the_plan(env_factory):
    """A provider row with a non-positive `max_inflight` used to refuse every
    attempt with NOTHING in flight — `0 >= -1` — and an admission decline opens
    no circuit and no block, so the plan waited forever with nothing to look at.
    Rows like this predate the API bound, so the resolver must ignore them."""
    from agent_orchestrator.domain.entities.ia_model import IAModel
    from agent_orchestrator.domain.entities.model_provider import ModelProvider

    agent = make_agent_spec().model_copy(
        update={"runtime_type": "pi", "provider_id": "openrouter", "model_id": "nemotron"}
    )
    env = env_factory({"g1t0": DummyBehavior(output="ok")}, agents=[agent])
    env.seed(_two_goal_cycle(env))

    catalog = _OneProviderCatalog(
        ModelProvider(
            id="openrouter",
            name="openrouter",
            base_url="http://x",
            api_key_ref="secret://x",
            max_inflight=-1,
            models=[IAModel(id="nemotron", name="nemotron", provider_id="openrouter")],
        )
    )
    handler = ExecutionHandler(
        env.runner,
        env.agents,
        env.ws,
        env.sink,
        env.clock,
        capacity=ProviderCapacityPolicy(max_inflight=8),
        providers=catalog,
    )

    assert (
        asyncio.run(handler.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value == "continue"
    )
    assert env.runner.calls == {"g1t0": 1}


def test_the_workspace_is_told_which_run_and_goal_an_attempt_belongs_to(env_factory):
    """The git-staging identity must reach the workspace, and be assertable.

    `NoOpWorkspace.begun` records only `(plan_id, task_id, attempt)`. Every
    other argument — `base_ref`, `cycle_id`, `goal_id`, `run_id` — was accepted
    and dropped, so a task branch cut from the WRONG base was inexpressible: no
    test could fail on it however carefully written, because the fake had
    already thrown the evidence away.

    This asserts against the full call record instead. It is the branch identity
    the whole rollback story rests on — a failed attempt leaves zero trace only
    if the attempt was isolated on its own branch in the first place.
    """
    env = env_factory()
    env.seed(_plan())

    assert asyncio.run(advance_plan("p1", *env.args)) == "continue"

    (call,) = env.ws.begin_calls
    assert call["plan_id"] == "p1"
    assert call["task_id"] == "t1"
    assert call["attempt"] == 1
    # The run id is what makes a task branch unique per attempt-run.
    assert call["run_id"], "the workspace was not told which run this attempt belongs to"


def test_the_agent_is_given_the_worktree_it_must_work_in(env_factory):
    """`DummyAgentRunner` was handed the workspace handle and discarded it, so
    "the agent worked outside its worktree" — the isolation that makes
    discard-on-failure a real rollback — could not be asserted at all."""
    env = env_factory()
    env.seed(_plan())

    assert asyncio.run(advance_plan("p1", *env.args)) == "continue"

    assert len(env.runner.workspaces) == 1
    assert env.runner.workspaces[0], "the agent was given no worktree path"


def _patiently_backing_off(plan: Plan) -> Plan:
    """The hour-scale curve a real rate limit produces.

    `_two_goal_cycle`'s 10-second backoff is BELOW
    `RoutingPolicy.downgrade_after_seconds`, where the design deliberately waits
    rather than degrading output — substituting a weaker model to dodge a
    ten-second 429 buys nothing. The case P8.6 Task 3 is about is the opposite
    one, and the only one the 2026-08-09 run actually produced: a wait long
    enough that routing around it is unambiguously right.
    """
    plan.retry_policy = RetryPolicy(
        max_attempts=6,
        initial_backoff_seconds=300,
        max_backoff_seconds=3_600,
        jitter_ratio=0,
    )
    return plan


def test_a_capacity_failure_reroutes_on_the_next_tick_instead_of_serving_its_backoff(
    env_factory,
):
    """P8.6 Task 3. Routing existed but could not reach the case it was for.

    `select_spec` reroutes around a busy provider, and every routing test above
    reaches it because the circuit was open BEFORE the task was picked. The
    live path is the other one: the task runs, the provider rate-limits it, and
    the finalizer requeues the task with the PATIENT capacity backoff on the
    plan's own `retry_not_before`. Navigation gates on that gate before
    `select_spec` is ever consulted, so the task sleeps out an hour-scale wait
    bound to the model that refused it while an interchangeable agent on a
    completely different provider sits idle. Measured as 42% of execution
    wall-clock in the 2026-08-09 latency analysis.

    The task-level wait is the thing that is wrong, not the circuit: the
    provider that refused genuinely needs its patient curve, and keeps it. What
    must not happen is the TASK inheriting that wait when it has somewhere else
    to run.
    """
    smart, cheap = _routing_agents()
    env = env_factory(
        {
            "g1t0": DummyBehavior(
                fail_times=1,
                fail_kind=FailureKind.RATE_LIMIT,
                fail_reason="429 temporarily rate-limited upstream",
                fail_limit_scope=LimitScope.UNKNOWN_CAPACITY,
            )
        },
        agents=[smart, cheap],
        default_agent_id="a1",
    )
    env.seed(_patiently_backing_off(_two_goal_cycle(env)))
    execution = ExecutionHandler(env.runner, env.agents, env.ws, env.sink, env.clock)

    # Attempt 1 runs on the bound smart model and is rate-limited.
    assert asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value == (
        "continue"
    )
    assert [spec.model_id for spec in env.runner.specs] == ["nemotron"]

    # The circuit for the provider that refused MUST still be armed and patient.
    with env.uow:
        circuit = env.uow.executions.get_runtime_circuit(
            "pi", "openrouter", None
        ) or env.uow.executions.get_runtime_circuit("pi", "openrouter", "nemotron")
    assert circuit is not None
    assert circuit.retry_at > env.clock.now(), "the busy provider keeps its patient wait"

    # Now the point: WITHOUT advancing the clock at all, the next tick must run
    # the task on the free provider rather than report NOT_READY.
    assert asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value == (
        "continue"
    )
    assert [spec.model_id for spec in env.runner.specs] == ["nemotron", "qwen"]


def test_the_reroute_leaves_the_persisted_binding_alone(env_factory):
    """Routing is a per-attempt substitution. The task's stored `agent_id` and
    `role_agent_ids` record the OPERATOR's preference and belong to the
    aggregate; rewriting them to whatever was free during one outage would make
    a transient capacity event permanently change what the plan says it wants."""
    smart, cheap = _routing_agents()
    env = env_factory(
        {
            "g1t0": DummyBehavior(
                fail_times=1,
                fail_kind=FailureKind.RATE_LIMIT,
                fail_reason="429 temporarily rate-limited upstream",
                fail_limit_scope=LimitScope.UNKNOWN_CAPACITY,
            )
        },
        agents=[smart, cheap],
        default_agent_id="a1",
    )
    env.seed(_patiently_backing_off(_two_goal_cycle(env)))
    execution = ExecutionHandler(env.runner, env.agents, env.ws, env.sink, env.clock)

    asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow))
    asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow))

    stored = env.stored("p1")
    task = {g.id: g for g in stored.execution_goals}["g1"].tasks[0]
    assert task.agent_id == "a1", "the reroute rewrote the persisted binding"
    # What actually ran stays auditable on the attempt ledger instead. Ordered
    # by the monotonic attempt `number`, not by list order: FakeClock stamps
    # both attempts with the same `started_at`, and `list_attempts` tiebreaks on
    # a random uuid, so list order is genuinely undefined here.
    with env.uow:
        attempts = sorted(env.uow.executions.list_attempts("p1"), key=lambda a: a.number)
    assert [a.model_id for a in attempts] == ["nemotron", "qwen"]


def test_a_capacity_failure_with_nowhere_to_go_still_waits_patiently(env_factory):
    """The other half, and the one that must not regress. With no free
    alternative there is nothing to route to, so dropping the task's backoff
    would just spin it against a provider that is still refusing. It keeps the
    patient wait it always had."""
    smart, _ = _routing_agents()
    env = env_factory(
        {
            "g1t0": DummyBehavior(
                fail_times=1,
                fail_kind=FailureKind.RATE_LIMIT,
                fail_reason="429 temporarily rate-limited upstream",
                fail_limit_scope=LimitScope.UNKNOWN_CAPACITY,
            )
        },
        agents=[smart],  # a roster of one: nowhere to reroute
        default_agent_id="a1",
    )
    env.seed(_patiently_backing_off(_two_goal_cycle(env)))
    execution = ExecutionHandler(env.runner, env.agents, env.ws, env.sink, env.clock)

    asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow))
    assert asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value == (
        "not_ready"
    )
    assert [spec.model_id for spec in env.runner.specs] == ["nemotron"]  # not re-fired

    stored = env.stored("p1")
    task = {g.id: g for g in stored.execution_goals}["g1"].tasks[0]
    assert task.retry_not_before is not None
    assert task.retry_not_before > env.clock.now()


def test_a_non_capacity_failure_still_serves_its_own_backoff(env_factory):
    """The reroute is scoped to CAPACITY. An agent that failed on its own
    merits — a tool error, a bad patch — has not been refused by anyone, and
    handing it straight to a different model would burn a second provider on a
    task that needs its backoff and its retry budget."""
    smart, cheap = _routing_agents()
    env = env_factory(
        {"g1t0": DummyBehavior(fail_times=1, fail_kind=FailureKind.TOOL_ERROR)},
        agents=[smart, cheap],
        default_agent_id="a1",
    )
    env.seed(_patiently_backing_off(_two_goal_cycle(env)))
    execution = ExecutionHandler(env.runner, env.agents, env.ws, env.sink, env.clock)

    asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow))
    assert asyncio.run(execution.handle_goal("p1", "g1", env.stored("p1"), env.uow)).value == (
        "not_ready"
    )
    assert [spec.model_id for spec in env.runner.specs] == ["nemotron"]
