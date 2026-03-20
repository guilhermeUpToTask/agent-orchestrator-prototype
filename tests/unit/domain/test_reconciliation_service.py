"""
tests/unit/domain/test_reconciliation_service.py

Tests for the pure domain reconciliation service.
ReconciliationService.assess() is side-effect free — it only inspects
task state and returns a ReconciliationDecision.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from src.domain import AgentProps, Assignment, TaskAggregate, TaskStatus
from src.domain import AgentSelector, ExecutionSpec
from src.domain.services.reconciler import (
    ReconciliationAction,
    ReconciliationService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_task(
    status: TaskStatus = TaskStatus.CREATED,
    updated_at_age_seconds: float = 0,
) -> TaskAggregate:
    task = TaskAggregate(
        task_id="task-001",
        feature_id="feat-x",
        title="T",
        description="D",
        agent_selector=AgentSelector(required_capability="code"),
        execution=ExecutionSpec(type="code"),
        status=status,
    )
    if updated_at_age_seconds:
        task.updated_at = datetime.now(timezone.utc) - timedelta(seconds=updated_at_age_seconds)
    return task


def make_agent(alive: bool = True) -> AgentProps:
    hb = datetime.now(timezone.utc) - timedelta(seconds=(5 if alive else 300))
    return AgentProps(agent_id="a-001", name="A", last_heartbeat=hb)


SVC = ReconciliationService(stuck_task_min_age_seconds=120)


# ---------------------------------------------------------------------------
# Terminal statuses → always NO_ACTION
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [
    TaskStatus.SUCCEEDED, TaskStatus.FAILED,
    TaskStatus.CANCELED, TaskStatus.MERGED,
])
def test_terminal_tasks_no_action(status):
    task = make_task(status=status)
    d = SVC.assess(task, lease_active=False)
    assert d.action == ReconciliationAction.NO_ACTION


# ---------------------------------------------------------------------------
# CREATED / REQUEUED — stuck pending
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [TaskStatus.CREATED, TaskStatus.REQUEUED])
def test_stuck_pending_returns_republish(status):
    task = make_task(status=status, updated_at_age_seconds=200)
    d = SVC.assess(task, lease_active=False)
    assert d.action == ReconciliationAction.REPUBLISH_PENDING


def test_republish_reason_is_created_event():
    task = make_task(status=TaskStatus.CREATED, updated_at_age_seconds=200)
    d = SVC.assess(task, lease_active=False)
    assert d.reason == "task.created"


def test_republish_reason_is_requeued_event():
    task = make_task(status=TaskStatus.REQUEUED, updated_at_age_seconds=200)
    d = SVC.assess(task, lease_active=False)
    assert d.reason == "task.requeued"


def test_fresh_pending_no_action():
    task = make_task(status=TaskStatus.CREATED, updated_at_age_seconds=10)
    d = SVC.assess(task, lease_active=True)
    assert d.action == ReconciliationAction.NO_ACTION


# ---------------------------------------------------------------------------
# ASSIGNED — dead agent
# ---------------------------------------------------------------------------

def test_assigned_dead_agent_returns_fail():
    task = make_task(status=TaskStatus.ASSIGNED)
    task.assignment = Assignment(agent_id="a-001")
    d = SVC.assess(task, lease_active=True, agent=make_agent(alive=False))
    assert d.action == ReconciliationAction.FAIL_DEAD_AGENT
    assert "a-001" in d.reason


def test_assigned_live_agent_active_lease_no_action():
    task = make_task(status=TaskStatus.ASSIGNED)
    task.assignment = Assignment(agent_id="a-001")
    d = SVC.assess(task, lease_active=True, agent=make_agent(alive=True))
    assert d.action == ReconciliationAction.NO_ACTION


# ---------------------------------------------------------------------------
# ASSIGNED — lease expired (agent alive but lease gone)
# ---------------------------------------------------------------------------

def test_assigned_expired_lease_returns_fail():
    task = make_task(status=TaskStatus.ASSIGNED)
    task.assignment = Assignment(agent_id="a-001")
    d = SVC.assess(task, lease_active=False, agent=make_agent(alive=True))
    assert d.action == ReconciliationAction.FAIL_LEASE_EXPIRED
    assert "ASSIGNED" in d.reason


# ---------------------------------------------------------------------------
# IN_PROGRESS — lease expired
# ---------------------------------------------------------------------------

def test_in_progress_expired_lease_returns_fail():
    task = make_task(status=TaskStatus.IN_PROGRESS)
    d = SVC.assess(task, lease_active=False)
    assert d.action == ReconciliationAction.FAIL_LEASE_EXPIRED
    assert "IN_PROGRESS" in d.reason


def test_in_progress_active_lease_no_action():
    task = make_task(status=TaskStatus.IN_PROGRESS)
    d = SVC.assess(task, lease_active=True)
    assert d.action == ReconciliationAction.NO_ACTION


# ---------------------------------------------------------------------------
# SUCCEEDED without commit_sha
# ---------------------------------------------------------------------------

def test_succeeded_no_commit_sha_returns_warn():
    from src.domain.value_objects.task import TaskResult
    task = make_task(status=TaskStatus.SUCCEEDED)
    task.result = TaskResult(commit_sha=None)
    d = SVC.assess(task, lease_active=True)
    assert d.action == ReconciliationAction.WARN_NO_COMMIT


def test_succeeded_with_commit_sha_no_action():
    from src.domain.value_objects.task import TaskResult
    task = make_task(status=TaskStatus.SUCCEEDED)
    task.result = TaskResult(commit_sha="abc123")
    d = SVC.assess(task, lease_active=True)
    assert d.action == ReconciliationAction.NO_ACTION


# ---------------------------------------------------------------------------
# ReconciliationDecision is immutable (frozen dataclass)
# ---------------------------------------------------------------------------

def test_decision_is_frozen():
    d = SVC.assess(make_task(status=TaskStatus.IN_PROGRESS), lease_active=True)
    with pytest.raises((AttributeError, TypeError)):
        d.action = ReconciliationAction.WARN_NO_COMMIT  # type: ignore


# ---------------------------------------------------------------------------
# ASSIGNED task with no assignment object (inconsistent/partial state)
# ---------------------------------------------------------------------------

def test_assigned_without_assignment_object_returns_no_action():
    """
    A task in ASSIGNED status but with assignment=None (can occur during a
    partial CAS write) should be silently skipped, not crash.
    """
    task = make_task(status=TaskStatus.ASSIGNED)
    task.assignment = None
    d = SVC.assess(task, lease_active=False)
    assert d.action == ReconciliationAction.NO_ACTION
