"""
tests/unit/domain/test_task_reclaim.py — liveness reclaim transition.

A reclaim (lease-expiry / dead-agent requeue) returns the task to the queue
WITHOUT touching the genuine-failure retry budget, tracked by its own
reclaim_count with a generous cap.
"""
from __future__ import annotations

import pytest

from src.domain import AgentSelector, Assignment, ExecutionSpec, TaskAggregate, TaskStatus
from src.domain.aggregates.task import MAX_RECLAIMS


def _assigned_task(status: TaskStatus = TaskStatus.ASSIGNED) -> TaskAggregate:
    t = TaskAggregate(
        task_id="t-1", feature_id="g-1", title="T", description="D",
        agent_selector=AgentSelector(required_capability="code:backend"),
        execution=ExecutionSpec(type="code:backend"),
        status=status,
    )
    t.assignment = Assignment(agent_id="a-1", lease_seconds=300)
    return t


def test_reclaim_requeues_without_touching_retry_budget():
    t = _assigned_task()
    attempts_before = t.retry_policy.attempt

    t.reclaim("Lease expired while ASSIGNED")

    assert t.status == TaskStatus.REQUEUED
    assert t.assignment is None
    assert t.reclaim_count == 1
    assert t.retry_policy.attempt == attempts_before  # genuine-failure budget untouched
    assert any(h.event == "task.reclaimed" for h in t.history)


def test_reclaim_from_in_progress():
    t = _assigned_task(status=TaskStatus.IN_PROGRESS)
    t.reclaim("Lease expired while IN_PROGRESS")
    assert t.status == TaskStatus.REQUEUED


def test_reclaim_rejected_from_non_active_status():
    t = _assigned_task(status=TaskStatus.CREATED)
    with pytest.raises(Exception):
        t.reclaim("nope")


def test_can_reclaim_caps_at_max():
    t = _assigned_task()
    assert t.can_reclaim() is True
    t.reclaim_count = MAX_RECLAIMS
    assert t.can_reclaim() is False
