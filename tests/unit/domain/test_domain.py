"""
tests/unit/domain/test_domain.py — Tests for the new DDD domain layer.

Covers:
  - AgentProps entity behaviours (is_alive, satisfies_version, matches_selector, scheduling_score)
  - RetryPolicy value object (can_retry, increment)
  - ExecutionSpec value object (validate_modifications)
  - TaskAggregate rich domain queries (is_assignable, is_stuck_pending, is_lease_expired,
    is_assigned_to_dead_agent, needs_retry, needs_cancel, is_ready_for_dispatch)
  - TaskRules (business rule delegation)
  - Domain error hierarchy
  - TaskStatus convenience sets
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from src.domain import (
    AgentProps,
    AgentSelector,
    Assignment,
    ExecutionSpec,
    ForbiddenFileEditError,
    InvalidStatusTransitionError,
    MaxRetriesExceededError,
    RetryPolicy,
    TaskAggregate,
    TaskStatus,
    TrustLevel,
)
from src.domain.rules import TaskRules


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_agent(
    agent_id: str = "a-001",
    capabilities: list[str] | None = None,
    version: str = "1.0.0",
    trust: TrustLevel = TrustLevel.MEDIUM,
    active: bool = True,
    heartbeat_age_seconds: float | None = 10.0,
    max_concurrent: int = 1,
    tools: list[str] | None = None,
) -> AgentProps:
    hb = None
    if heartbeat_age_seconds is not None:
        hb = datetime.now(timezone.utc) - timedelta(seconds=heartbeat_age_seconds)
    return AgentProps(
        agent_id=agent_id,
        name=f"Agent {agent_id}",
        capabilities=capabilities or ["backend_dev"],
        version=version,
        trust_level=trust,
        active=active,
        last_heartbeat=hb,
        max_concurrent_tasks=max_concurrent,
        tools=tools or [],
    )


def make_task(
    status: TaskStatus = TaskStatus.CREATED,
    max_retries: int = 2,
    depends_on: list[str] | None = None,
    updated_at_age_seconds: float | None = None,
) -> TaskAggregate:
    task = TaskAggregate(
        task_id="task-001",
        feature_id="feat-x",
        title="T",
        description="D",
        agent_selector=AgentSelector(required_capability="backend_dev"),
        execution=ExecutionSpec(type="code"),
        status=status,
        retry_policy=RetryPolicy(max_retries=max_retries),
        depends_on=depends_on or [],
    )
    if updated_at_age_seconds is not None:
        task.updated_at = datetime.now(timezone.utc) - timedelta(seconds=updated_at_age_seconds)
    return task


# ===========================================================================
# TaskStatus convenience sets
# ===========================================================================

class TestTaskStatusSets:

    def test_assignable_contains_created_and_requeued(self):
        assert TaskStatus.CREATED in TaskStatus.assignable()
        assert TaskStatus.REQUEUED in TaskStatus.assignable()

    def test_assignable_excludes_active_and_terminal(self):
        for s in (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS,
                  TaskStatus.SUCCEEDED, TaskStatus.FAILED,
                  TaskStatus.CANCELED, TaskStatus.MERGED):
            assert s not in TaskStatus.assignable()

    def test_active_contains_assigned_and_in_progress(self):
        assert TaskStatus.ASSIGNED in TaskStatus.active()
        assert TaskStatus.IN_PROGRESS in TaskStatus.active()

    def test_terminal_contains_four_statuses(self):
        assert len(TaskStatus.terminal()) == 4
        for s in (TaskStatus.SUCCEEDED, TaskStatus.FAILED,
                  TaskStatus.CANCELED, TaskStatus.MERGED):
            assert s in TaskStatus.terminal()


# ===========================================================================
# AgentProps entity behaviours
# ===========================================================================

class TestAgentPropsIsAlive:

    def test_recent_heartbeat_is_alive(self):
        assert make_agent(heartbeat_age_seconds=5).is_alive() is True

    def test_old_heartbeat_is_dead(self):
        assert make_agent(heartbeat_age_seconds=120).is_alive() is False

    def test_no_heartbeat_is_dead(self):
        assert make_agent(heartbeat_age_seconds=None).is_alive() is False

    def test_exactly_at_threshold_is_dead(self):
        assert make_agent(heartbeat_age_seconds=60).is_alive(threshold_seconds=60) is False

    def test_just_below_threshold_is_alive(self):
        assert make_agent(heartbeat_age_seconds=59).is_alive(threshold_seconds=60) is True

    def test_custom_threshold(self):
        agent = make_agent(heartbeat_age_seconds=100)
        assert agent.is_alive(threshold_seconds=200) is True
        assert agent.is_alive(threshold_seconds=50) is False


class TestAgentPropsSatisfiesVersion:

    @pytest.mark.parametrize("agent_ver,constraint,expected", [
        ("1.0.0", ">=1.0.0", True),
        ("1.5.0", ">=1.0.0", True),
        ("0.9.9", ">=1.0.0", False),
        ("1.0.0", ">=2.0.0", False),
        ("1.0.0", "1.0.0",   True),   # exact match
        ("1.0.1", "1.0.0",   False),  # exact match — different patch
        ("1.10.0", ">=1.9.0", True),  # 10 > 9
    ])
    def test_version_constraints(self, agent_ver, constraint, expected):
        agent = make_agent(version=agent_ver)
        assert agent.satisfies_version(constraint) == expected


class TestAgentPropsMatchesSelector:

    def test_eligible_agent_matches(self):
        agent = make_agent(capabilities=["backend_dev"], version="1.0.0")
        selector = AgentSelector(required_capability="backend_dev", min_version=">=1.0.0")
        assert agent.matches_selector(selector) is True

    def test_inactive_agent_does_not_match(self):
        agent = make_agent(active=False)
        assert agent.matches_selector(AgentSelector(required_capability="backend_dev")) is False

    def test_dead_agent_does_not_match(self):
        agent = make_agent(heartbeat_age_seconds=None)
        assert agent.matches_selector(AgentSelector(required_capability="backend_dev")) is False

    def test_missing_capability_does_not_match(self):
        agent = make_agent(capabilities=["frontend"])
        assert agent.matches_selector(AgentSelector(required_capability="backend_dev")) is False

    def test_version_too_low_does_not_match(self):
        agent = make_agent(version="0.9.0")
        assert agent.matches_selector(AgentSelector(required_capability="backend_dev", min_version=">=1.0.0")) is False


class TestAgentPropsSchedulingScore:

    def test_high_trust_scores_higher_than_low(self):
        high = make_agent(trust=TrustLevel.HIGH)
        low  = make_agent(trust=TrustLevel.LOW)
        assert high.scheduling_score() > low.scheduling_score()

    def test_more_tools_scores_higher_with_same_trust(self):
        few  = make_agent(tools=[])
        many = make_agent(tools=["git", "pytest", "docker"])
        assert many.scheduling_score() > few.scheduling_score()

    def test_trust_dominates_tool_count(self):
        low_many  = make_agent(trust=TrustLevel.LOW,  tools=[f"t{i}" for i in range(50)])
        high_none = make_agent(trust=TrustLevel.HIGH, tools=[])
        assert high_none.scheduling_score() > low_many.scheduling_score()


# ===========================================================================
# RetryPolicy value object
# ===========================================================================

class TestRetryPolicy:

    def test_can_retry_when_below_max(self):
        rp = RetryPolicy(max_retries=3, attempt=2)
        assert rp.can_retry() is True

    def test_cannot_retry_when_at_max(self):
        rp = RetryPolicy(max_retries=2, attempt=2)
        assert rp.can_retry() is False

    def test_increment_advances_attempt(self):
        rp = RetryPolicy(max_retries=3, attempt=0)
        rp.increment()
        assert rp.attempt == 1

    def test_increment_raises_when_exhausted(self):
        rp = RetryPolicy(max_retries=1, attempt=1)
        with pytest.raises(MaxRetriesExceededError):
            rp.increment()

    def test_increment_multiple_times(self):
        rp = RetryPolicy(max_retries=3, attempt=0)
        rp.increment()
        rp.increment()
        assert rp.attempt == 2


# ===========================================================================
# ExecutionSpec value object
# ===========================================================================

class TestExecutionSpec:

    def test_validate_modifications_passes_allowed_files(self):
        spec = ExecutionSpec(type="code", files_allowed_to_modify=["a.py", "b.py"])
        spec.validate_modifications(["a.py"])  # no exception

    def test_validate_modifications_raises_for_forbidden(self):
        spec = ExecutionSpec(type="code", files_allowed_to_modify=["a.py"])
        with pytest.raises(ForbiddenFileEditError) as exc_info:
            spec.validate_modifications(["a.py", "evil.py"])
        assert "evil.py" in exc_info.value.violations

    def test_validate_modifications_empty_allowed_raises_for_any_file(self):
        spec = ExecutionSpec(type="code", files_allowed_to_modify=[])
        with pytest.raises(ForbiddenFileEditError):
            spec.validate_modifications(["any.py"])

    def test_validate_modifications_empty_modified_never_raises(self):
        spec = ExecutionSpec(type="code", files_allowed_to_modify=[])
        spec.validate_modifications([])  # no exception


# ===========================================================================
# TaskAggregate rich domain queries
# ===========================================================================

class TestTaskAggregateIsAssignable:

    @pytest.mark.parametrize("status", [TaskStatus.CREATED, TaskStatus.REQUEUED])
    def test_assignable_statuses(self, status):
        assert make_task(status=status).is_assignable() is True

    @pytest.mark.parametrize("status", [
        TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS,
        TaskStatus.SUCCEEDED, TaskStatus.FAILED,
        TaskStatus.CANCELED, TaskStatus.MERGED,
    ])
    def test_non_assignable_statuses(self, status):
        assert make_task(status=status).is_assignable() is False


class TestTaskAggregateIsStuckPending:

    def test_stuck_when_old_enough(self):
        task = make_task(status=TaskStatus.CREATED, updated_at_age_seconds=200)
        assert task.is_stuck_pending(threshold_seconds=120) is True

    def test_not_stuck_when_fresh(self):
        task = make_task(status=TaskStatus.CREATED, updated_at_age_seconds=30)
        assert task.is_stuck_pending(threshold_seconds=120) is False

    def test_not_stuck_for_non_pending_status(self):
        task = make_task(status=TaskStatus.ASSIGNED, updated_at_age_seconds=300)
        assert task.is_stuck_pending(threshold_seconds=1) is False

    def test_requeued_also_considered_pending(self):
        task = make_task(status=TaskStatus.REQUEUED, updated_at_age_seconds=200)
        assert task.is_stuck_pending(threshold_seconds=120) is True


class TestTaskAggregateIsLeaseExpired:

    def test_expired_for_assigned_without_lease(self):
        task = make_task(status=TaskStatus.ASSIGNED)
        assert task.is_lease_expired(lease_active=False) is True

    def test_not_expired_for_assigned_with_lease(self):
        task = make_task(status=TaskStatus.ASSIGNED)
        assert task.is_lease_expired(lease_active=True) is False

    def test_expired_for_in_progress_without_lease(self):
        task = make_task(status=TaskStatus.IN_PROGRESS)
        assert task.is_lease_expired(lease_active=False) is True

    def test_not_expired_for_non_active_status(self):
        for status in (TaskStatus.CREATED, TaskStatus.SUCCEEDED, TaskStatus.FAILED):
            assert make_task(status=status).is_lease_expired(lease_active=False) is False


class TestTaskAggregateIsAssignedToDeadAgent:

    def test_dead_agent_detected(self):
        task = make_task(status=TaskStatus.ASSIGNED)
        task.assignment = Assignment(agent_id="a-001")
        dead_agent = make_agent(heartbeat_age_seconds=None)
        assert task.is_assigned_to_dead_agent(dead_agent) is True

    def test_live_agent_not_detected(self):
        task = make_task(status=TaskStatus.ASSIGNED)
        task.assignment = Assignment(agent_id="a-001")
        live_agent = make_agent(heartbeat_age_seconds=5)
        assert task.is_assigned_to_dead_agent(live_agent) is False

    def test_none_agent_not_detected(self):
        task = make_task(status=TaskStatus.ASSIGNED)
        task.assignment = Assignment(agent_id="a-001")
        assert task.is_assigned_to_dead_agent(None) is False

    def test_not_assigned_status_always_false(self):
        task = make_task(status=TaskStatus.IN_PROGRESS)
        dead_agent = make_agent(heartbeat_age_seconds=None)
        assert task.is_assigned_to_dead_agent(dead_agent) is False


class TestTaskAggregateNeedsRetryCancel:

    def test_needs_retry_when_failed_with_budget(self):
        task = make_task(status=TaskStatus.FAILED, max_retries=2)
        assert task.needs_retry() is True

    def test_needs_cancel_when_failed_budget_exhausted(self):
        task = make_task(status=TaskStatus.FAILED, max_retries=2)
        task.retry_policy.attempt = 2
        assert task.needs_cancel() is True
        assert task.needs_retry() is False

    def test_needs_retry_false_when_not_failed(self):
        assert make_task(status=TaskStatus.CREATED).needs_retry() is False

    def test_needs_cancel_false_when_not_failed(self):
        assert make_task(status=TaskStatus.CREATED).needs_cancel() is False


class TestTaskAggregateIsReadyForDispatch:

    def test_ready_when_created_and_deps_satisfied(self):
        task = make_task(status=TaskStatus.CREATED, depends_on=["t-A"])
        assert task.is_ready_for_dispatch({"t-A"}) is True

    def test_not_ready_when_deps_missing(self):
        task = make_task(status=TaskStatus.CREATED, depends_on=["t-A", "t-B"])
        assert task.is_ready_for_dispatch({"t-A"}) is False

    def test_ready_when_no_deps(self):
        task = make_task(status=TaskStatus.CREATED)
        assert task.is_ready_for_dispatch(set()) is True

    def test_not_ready_when_not_created(self):
        task = make_task(status=TaskStatus.REQUEUED, depends_on=[])
        assert task.is_ready_for_dispatch(set()) is False


# ===========================================================================
# Domain errors
# ===========================================================================

class TestDomainErrors:

    def test_invalid_status_transition_error_message(self):
        err = InvalidStatusTransitionError("t-1", "created", ["assigned", "requeued"])
        assert "t-1" in str(err)
        assert "created" in str(err)

    def test_invalid_status_is_value_error(self):
        err = InvalidStatusTransitionError("t-1", "created", [])
        assert isinstance(err, ValueError)

    def test_max_retries_exceeded_message(self):
        err = MaxRetriesExceededError("t-1", attempt=3, max_retries=3)
        assert "t-1" in str(err)
        assert "3" in str(err)

    def test_max_retries_is_value_error(self):
        err = MaxRetriesExceededError("t-1", attempt=1, max_retries=1)
        assert isinstance(err, ValueError)

    def test_forbidden_file_error_stores_violations(self):
        err = ForbiddenFileEditError(["secret.py", "config.yaml"])
        assert "secret.py" in err.violations
        assert "config.yaml" in err.violations

    def test_transition_error_raised_by_aggregate(self):
        task = make_task(status=TaskStatus.SUCCEEDED)
        with pytest.raises(InvalidStatusTransitionError):
            task.start()


# ===========================================================================
# TaskRules delegation
# ===========================================================================

class TestTaskRules:

    def test_is_stuck_pending_delegates_to_aggregate(self):
        task = make_task(status=TaskStatus.CREATED, updated_at_age_seconds=200)
        assert TaskRules.is_stuck_pending(task, 120) is True

    def test_is_lease_expired_delegates_to_aggregate(self):
        task = make_task(status=TaskStatus.IN_PROGRESS)
        assert TaskRules.is_lease_expired(task, lease_active=False) is True

    def test_is_assigned_to_dead_agent_delegates(self):
        task = make_task(status=TaskStatus.ASSIGNED)
        task.assignment = Assignment(agent_id="a")
        assert TaskRules.is_assigned_to_dead_agent(task, make_agent(heartbeat_age_seconds=None)) is True

    def test_should_unblock_dependent(self):
        task = make_task(status=TaskStatus.CREATED, depends_on=["t-A"])
        assert TaskRules.should_unblock_dependent(task, {"t-A"}) is True

    def test_should_requeue_after_failure(self):
        task = make_task(status=TaskStatus.FAILED, max_retries=2)
        assert TaskRules.should_requeue_after_failure(task) is True

    def test_should_cancel_after_failure(self):
        task = make_task(status=TaskStatus.FAILED, max_retries=0)
        assert TaskRules.should_cancel_after_failure(task) is True

    def test_should_requeue_on_lease_expiry(self):
        task = make_task(status=TaskStatus.ASSIGNED, max_retries=2)
        assert TaskRules.should_requeue_on_lease_expiry(task, lease_active=False) is True

    def test_should_not_requeue_when_retries_exhausted(self):
        task = make_task(status=TaskStatus.ASSIGNED, max_retries=2)
        task.retry_policy.attempt = 2
        assert TaskRules.should_requeue_on_lease_expiry(task, lease_active=False) is False

    def test_should_fail_on_lease_expiry(self):
        task = make_task(status=TaskStatus.IN_PROGRESS)
        assert TaskRules.should_fail_on_lease_expiry(task, lease_active=False) is True

    def test_should_not_fail_when_lease_active(self):
        task = make_task(status=TaskStatus.IN_PROGRESS)
        assert TaskRules.should_fail_on_lease_expiry(task, lease_active=True) is False
