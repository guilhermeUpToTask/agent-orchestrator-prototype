"""Operator-tunable retry policy on an already-persisted plan (un-freeze #12),
on both backends via env_factory: a partial update merges over the plan's
current policy, is legal even while blocked/paused, and never touches any
in-flight task's already-computed attempt count or armed retry_not_before —
only the domain unfreeze's blocked path guard (a legacy-terminal plan) is
rejected."""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import PlanStatus
from src.domain.entities.task import Task
from src.domain.errors.planning_errors import PlanAlreadyTerminalError
from src.domain.policies.retry_policies import RetryPolicy
from src.domain.value_objects.lifecycle import Status

from src.app.use_cases.update_retry_policy import update_retry_policy


def task(tid: str, position: int, **kwargs) -> Task:
    return Task(id=tid, name=tid, position=position, description="", agent_id="a1", **kwargs)


def running_plan(
    goals: list[Goal] | None = None, phase: PlanPhase = PlanPhase.RUNNING, **kwargs
) -> Plan:
    return Plan(
        project_id="project-1",
        id="p1",
        brief="b",
        phase=phase,
        goals=goals
        if goals is not None
        else [Goal(id="g1", name="g1", position=0, description="", tasks=[task("t0", 0)])],
        **kwargs,
    )


def test_partial_update_merges_over_current_policy(env_factory):
    env = env_factory()
    env.seed(running_plan(retry_policy=RetryPolicy(max_attempts=3, max_backoff_seconds=900)))

    update_retry_policy("p1", {"max_attempts": 20}, env.uow)

    stored = env.stored("p1")
    assert stored.retry_policy.max_attempts == 20
    # untouched field keeps its prior (non-default) value, not reset to bare default
    assert stored.retry_policy.max_backoff_seconds == 900


def test_all_fields_can_be_overridden_together(env_factory):
    env = env_factory()
    env.seed(running_plan())

    update_retry_policy(
        "p1",
        {
            "max_attempts": 15,
            "initial_backoff_seconds": 5.0,
            "backoff_multiplier": 1.5,
            "max_backoff_seconds": 3600.0,
            "jitter_ratio": 0.1,
        },
        env.uow,
    )

    stored = env.stored("p1")
    assert stored.retry_policy == RetryPolicy(
        max_attempts=15,
        initial_backoff_seconds=5.0,
        backoff_multiplier=1.5,
        max_backoff_seconds=3600.0,
        jitter_ratio=0.1,
    )


def test_legal_while_blocked_and_paused(env_factory):
    env = env_factory()
    plan = running_plan()
    plan.status = PlanStatus.BLOCKED
    plan.paused = True
    env.seed(plan)

    update_retry_policy("p1", {"max_attempts": 10}, env.uow)

    assert env.stored("p1").retry_policy.max_attempts == 10


def test_does_not_touch_inflight_task_attempt_state(env_factory):
    env = env_factory()
    not_before = env.clock.now() + timedelta(seconds=120)
    gated = task("t0", 0, status=Status.FAILED, attempt=2, retry_not_before=not_before)
    plan = running_plan(goals=[Goal(id="g1", name="g1", position=0, description="", tasks=[gated])])
    env.seed(plan)

    update_retry_policy("p1", {"max_attempts": 25}, env.uow)

    stored_task = env.stored("p1").goals[0].tasks[0]
    assert stored_task.attempt == 2
    assert stored_task.retry_not_before == not_before
    assert stored_task.status == Status.FAILED


def test_rejected_on_a_legacy_terminal_plan(env_factory):
    env = env_factory()
    plan = running_plan(phase=PlanPhase.DONE)
    env.seed(plan)

    with pytest.raises(PlanAlreadyTerminalError):
        update_retry_policy("p1", {"max_attempts": 10}, env.uow)


def test_no_outbox_event_emitted(env_factory):
    """Editorial mutation, same shape as apply_edit: no domain event, just the
    version bump — this is a config-shaped field, not a lifecycle transition."""
    env = env_factory()
    env.seed(running_plan())

    update_retry_policy("p1", {"max_attempts": 10}, env.uow)

    assert env.outbox_types() == []
