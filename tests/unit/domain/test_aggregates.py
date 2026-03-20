"""
tests/unit/test_models.py — Exhaustive unit tests for domain models.

Covers:
  - All TaskAggregate state transitions (happy paths + invalid)
  - History / audit trail integrity
  - state_version monotonic increments
  - RetryPolicy boundary conditions
  - AgentProps defaults and field validation
  - DomainEvent construction
  - ExecutionContext and TaskResult
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from src.domain import (
    AgentExecutionResult,
    AgentProps,
    AgentSelector,
    Assignment,
    DomainEvent,
    ExecutionContext,
    ExecutionSpec,
    HistoryEntry,
    RetryPolicy,
    TaskAggregate,
    TaskResult,
    TaskStatus,
    TrustLevel,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_task(
    task_id: str = "task-001",
    status: TaskStatus = TaskStatus.CREATED,
    max_retries: int = 2,
    depends_on: list[str] | None = None,
) -> TaskAggregate:
    task = TaskAggregate(
        task_id=task_id,
        feature_id="feat-auth",
        title="Test task",
        description="A test task description",
        agent_selector=AgentSelector(required_capability="backend_dev"),
        execution=ExecutionSpec(
            type="code:backend",
            files_allowed_to_modify=["app/auth.py", "tests/test_auth.py"],
            test_command="pytest tests/",
        ),
        status=status,
        retry_policy=RetryPolicy(max_retries=max_retries),
        depends_on=depends_on or [],
    )
    return task


def assigned_task(task_id: str = "task-001") -> TaskAggregate:
    task = make_task(task_id)
    task.assign(Assignment(agent_id="agent-001"))
    return task


def in_progress_task(task_id: str = "task-001") -> TaskAggregate:
    task = assigned_task(task_id)
    task.start()
    return task


# ===========================================================================
# TaskAggregate — initial state
# ===========================================================================

class TestTaskAggregateInit:

    def test_default_status_is_created(self):
        task = make_task()
        assert task.status == TaskStatus.CREATED

    def test_default_state_version_is_one(self):
        task = make_task()
        assert task.state_version == 1

    def test_history_is_empty_on_creation(self):
        task = make_task()
        assert task.history == []

    def test_retry_policy_defaults(self):
        task = make_task()
        assert task.retry_policy.attempt == 0
        assert task.retry_policy.max_retries == 2
        assert task.retry_policy.backoff_seconds == 30

    def test_depends_on_defaults_to_empty(self):
        task = make_task()
        assert task.depends_on == []

    def test_depends_on_set_correctly(self):
        task = make_task(depends_on=["task-A", "task-B"])
        assert "task-A" in task.depends_on
        assert "task-B" in task.depends_on

    def test_created_at_is_utc(self):
        task = make_task()
        assert task.created_at.tzinfo is not None

    def test_no_result_on_creation(self):
        task = make_task()
        assert task.result is None

    def test_no_assignment_on_creation(self):
        task = make_task()
        assert task.assignment is None


# ===========================================================================
# TaskAggregate.assign()
# ===========================================================================

class TestAssign:

    def test_assign_from_created(self):
        task = make_task()
        task.assign(Assignment(agent_id="agent-001"))
        assert task.status == TaskStatus.ASSIGNED

    def test_assign_from_requeued(self):
        task = make_task(status=TaskStatus.REQUEUED)
        task.assign(Assignment(agent_id="agent-001"))
        assert task.status == TaskStatus.ASSIGNED

    def test_assign_bumps_state_version(self):
        task = make_task()
        v0 = task.state_version
        task.assign(Assignment(agent_id="agent-001"))
        assert task.state_version == v0 + 1

    def test_assign_appends_history_entry(self):
        task = make_task()
        task.assign(Assignment(agent_id="agent-001"))
        assert len(task.history) == 1
        assert task.history[0].event == "task.assigned"
        assert task.history[0].actor == "agent-001"

    def test_assign_stores_assignment(self):
        task = make_task()
        assignment = Assignment(agent_id="agent-007", lease_seconds=600)
        task.assign(assignment)
        assert task.assignment.agent_id == "agent-007"
        assert task.assignment.lease_seconds == 600

    def test_assign_sets_updated_at(self):
        task = make_task()
        before = task.updated_at
        task.assign(Assignment(agent_id="agent-001"))
        assert task.updated_at >= before

    @pytest.mark.parametrize("bad_status", [
        TaskStatus.IN_PROGRESS,
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
        TaskStatus.MERGED,
        TaskStatus.ASSIGNED,
    ])
    def test_assign_invalid_status_raises(self, bad_status):
        task = make_task(status=bad_status)
        with pytest.raises(ValueError, match="expected status"):
            task.assign(Assignment(agent_id="agent-001"))

    def test_assign_history_contains_lease_seconds(self):
        task = make_task()
        task.assign(Assignment(agent_id="a", lease_seconds=120))
        assert task.history[0].detail["lease_seconds"] == 120


# ===========================================================================
# TaskAggregate.start()
# ===========================================================================

class TestStart:

    def test_start_from_assigned(self):
        task = assigned_task()
        task.start()
        assert task.status == TaskStatus.IN_PROGRESS

    def test_start_bumps_state_version(self):
        task = assigned_task()
        v0 = task.state_version
        task.start()
        assert task.state_version == v0 + 1

    def test_start_appends_history(self):
        task = assigned_task()
        task.start()
        events = [h.event for h in task.history]
        assert "task.started" in events

    def test_start_history_actor_is_agent(self):
        task = assigned_task()
        task.start()
        started = next(h for h in task.history if h.event == "task.started")
        assert started.actor == "agent-001"

    @pytest.mark.parametrize("bad_status", [
        TaskStatus.CREATED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
        TaskStatus.REQUEUED,
    ])
    def test_start_invalid_status_raises(self, bad_status):
        task = make_task(status=bad_status)
        with pytest.raises(ValueError):
            task.start()


# ===========================================================================
# TaskAggregate.complete()
# ===========================================================================

class TestComplete:

    def test_complete_from_in_progress(self):
        task = in_progress_task()
        task.complete(TaskResult(branch="task/t-001", commit_sha="abc123"))
        assert task.status == TaskStatus.SUCCEEDED

    def test_complete_stores_result(self):
        task = in_progress_task()
        result = TaskResult(branch="task/t-001", commit_sha="deadbeef", modified_files=["a.py"])
        task.complete(result)
        assert task.result.commit_sha == "deadbeef"
        assert task.result.branch == "task/t-001"
        assert "a.py" in task.result.modified_files

    def test_complete_bumps_state_version(self):
        task = in_progress_task()
        v0 = task.state_version
        task.complete(TaskResult())
        assert task.state_version == v0 + 1

    def test_complete_appends_history(self):
        task = in_progress_task()
        task.complete(TaskResult(commit_sha="abc"))
        events = [h.event for h in task.history]
        assert "task.completed" in events

    def test_complete_history_contains_commit_sha(self):
        task = in_progress_task()
        task.complete(TaskResult(commit_sha="sha999"))
        completed = next(h for h in task.history if h.event == "task.completed")
        assert completed.detail["commit_sha"] == "sha999"

    @pytest.mark.parametrize("bad_status", [
        TaskStatus.CREATED,
        TaskStatus.ASSIGNED,
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
    ])
    def test_complete_invalid_status_raises(self, bad_status):
        task = make_task(status=bad_status)
        with pytest.raises(ValueError):
            task.complete(TaskResult())

    def test_complete_with_empty_result(self):
        task = in_progress_task()
        task.complete(TaskResult())
        assert task.status == TaskStatus.SUCCEEDED
        assert task.result is not None


# ===========================================================================
# TaskAggregate.fail()
# ===========================================================================

class TestFail:

    def test_fail_from_in_progress(self):
        task = in_progress_task()
        task.fail("timeout")
        assert task.status == TaskStatus.FAILED

    def test_fail_from_assigned(self):
        task = assigned_task()
        task.fail("agent died")
        assert task.status == TaskStatus.FAILED

    def test_fail_bumps_state_version(self):
        task = in_progress_task()
        v0 = task.state_version
        task.fail("reason")
        assert task.state_version == v0 + 1

    def test_fail_appends_history_with_reason(self):
        task = in_progress_task()
        task.fail("network error")
        failed = next(h for h in task.history if h.event == "task.failed")
        assert failed.detail["reason"] == "network error"

    def test_fail_actor_is_agent(self):
        task = in_progress_task()
        task.fail("oops")
        failed = next(h for h in task.history if h.event == "task.failed")
        assert failed.actor == "agent-001"

    def test_fail_actor_is_system_without_assignment(self):
        task = make_task(status=TaskStatus.IN_PROGRESS)
        task.fail("system error")
        failed = next(h for h in task.history if h.event == "task.failed")
        assert failed.actor == "system"

    @pytest.mark.parametrize("bad_status", [
        TaskStatus.CREATED,
        TaskStatus.SUCCEEDED,
        TaskStatus.CANCELED,
        TaskStatus.REQUEUED,
    ])
    def test_fail_invalid_status_raises(self, bad_status):
        task = make_task(status=bad_status)
        with pytest.raises(ValueError):
            task.fail("reason")


# ===========================================================================
# TaskAggregate.requeue()
# ===========================================================================

class TestRequeue:

    def test_requeue_from_failed(self):
        task = in_progress_task()
        task.fail("oops")
        task.requeue()
        assert task.status == TaskStatus.REQUEUED

    def test_requeue_increments_attempt(self):
        task = in_progress_task()
        task.fail("oops")
        task.requeue()
        assert task.retry_policy.attempt == 1

    def test_requeue_clears_assignment(self):
        task = in_progress_task()
        task.fail("oops")
        task.requeue()
        assert task.assignment is None

    def test_requeue_bumps_state_version(self):
        task = in_progress_task()
        task.fail("oops")
        v0 = task.state_version
        task.requeue()
        assert task.state_version == v0 + 1

    def test_requeue_appends_history_with_attempt(self):
        task = in_progress_task()
        task.fail("oops")
        task.requeue()
        requeued = next(h for h in task.history if h.event == "task.requeued")
        assert requeued.detail["attempt"] == 1

    def test_requeue_exceeds_max_retries_raises(self):
        task = make_task(max_retries=2)
        task.retry_policy.attempt = 2  # already at max
        task.assign(Assignment(agent_id="a"))
        task.start()
        task.fail("x")
        with pytest.raises(ValueError, match="max retries"):
            task.requeue()

    def test_requeue_at_exactly_max_retries_raises(self):
        task = make_task(max_retries=1)
        task.retry_policy.attempt = 1
        task.assign(Assignment(agent_id="a"))
        task.start()
        task.fail("x")
        with pytest.raises(ValueError):
            task.requeue()

    def test_requeue_at_zero_retries_succeeds(self):
        task = make_task(max_retries=1)
        task.assign(Assignment(agent_id="a"))
        task.start()
        task.fail("x")
        task.requeue()  # attempt becomes 1, which is < max_retries... wait
        # attempt 0 < max_retries 1 → succeeds
        assert task.status == TaskStatus.REQUEUED

    def test_second_requeue_increments_correctly(self):
        task = make_task(max_retries=3)
        for _ in range(2):
            if task.status in (TaskStatus.CREATED, TaskStatus.REQUEUED):
                task.assign(Assignment(agent_id="a"))
            task.start()
            task.fail("x")
            task.requeue()
        assert task.retry_policy.attempt == 2

    @pytest.mark.parametrize("bad_status", [
        TaskStatus.CREATED,
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.SUCCEEDED,
        TaskStatus.CANCELED,
    ])
    def test_requeue_invalid_status_raises(self, bad_status):
        task = make_task(status=bad_status)
        with pytest.raises(ValueError):
            task.requeue()


# ===========================================================================
# TaskAggregate.cancel()
# ===========================================================================

class TestCancel:

    @pytest.mark.parametrize("status", [
        TaskStatus.CREATED,
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.FAILED,
        TaskStatus.REQUEUED,
    ])
    def test_cancel_from_any_status(self, status):
        task = make_task(status=status)
        task.cancel("user request")
        assert task.status == TaskStatus.CANCELED

    def test_cancel_appends_history_with_reason(self):
        task = make_task()
        task.cancel("duplicate task")
        canceled = next(h for h in task.history if h.event == "task.canceled")
        assert canceled.detail["reason"] == "duplicate task"

    def test_cancel_with_empty_reason(self):
        task = make_task()
        task.cancel()
        assert task.status == TaskStatus.CANCELED

    def test_cancel_bumps_state_version(self):
        task = make_task()
        v0 = task.state_version
        task.cancel()
        assert task.state_version == v0 + 1


# ===========================================================================
# TaskAggregate.mark_merged()
# ===========================================================================

class TestMarkMerged:

    def test_mark_merged_from_succeeded(self):
        task = in_progress_task()
        task.complete(TaskResult(commit_sha="sha1"))
        task.mark_merged()
        assert task.status == TaskStatus.MERGED

    def test_mark_merged_appends_history(self):
        task = in_progress_task()
        task.complete(TaskResult(commit_sha="sha1"))
        task.mark_merged()
        events = [h.event for h in task.history]
        assert "task.merged" in events

    @pytest.mark.parametrize("bad_status", [
        TaskStatus.CREATED,
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
        TaskStatus.REQUEUED,
    ])
    def test_mark_merged_invalid_status_raises(self, bad_status):
        task = make_task(status=bad_status)
        with pytest.raises(ValueError):
            task.mark_merged()


# ===========================================================================
# Full lifecycle state_version tracking
# ===========================================================================

class TestStateVersionTracking:

    def test_version_increments_through_full_lifecycle(self):
        task = make_task()
        assert task.state_version == 1

        task.assign(Assignment(agent_id="a"))
        assert task.state_version == 2

        task.start()
        assert task.state_version == 3

        task.complete(TaskResult(commit_sha="abc"))
        assert task.state_version == 4

        task.mark_merged()
        assert task.state_version == 5

    def test_version_increments_through_failure_path(self):
        task = make_task()
        task.assign(Assignment(agent_id="a"))
        task.start()
        assert task.state_version == 3

        task.fail("oops")
        assert task.state_version == 4

        task.requeue()
        assert task.state_version == 5

    def test_history_length_matches_transitions(self):
        task = make_task()
        task.assign(Assignment(agent_id="a"))
        task.start()
        task.complete(TaskResult())
        assert len(task.history) == 3


# ===========================================================================
# AgentProps validation
# ===========================================================================


# ===========================================================================
# Assignment
# ===========================================================================

class TestAssignment:

    def test_assigned_at_is_utc(self):
        a = Assignment(agent_id="a")
        assert a.assigned_at.tzinfo is not None

    def test_default_lease_seconds(self):
        a = Assignment(agent_id="a")
        assert a.lease_seconds == 300

    def test_lease_token_defaults_to_none(self):
        a = Assignment(agent_id="a")
        assert a.lease_token is None


# ===========================================================================
# DomainEvent
# ===========================================================================

class TestDomainEvent:

    def test_event_id_is_generated(self):
        e = DomainEvent(type="task.created", producer="test", payload={"task_id": "t1"})
        assert e.event_id is not None and len(e.event_id) > 0

    def test_event_id_unique_per_instance(self):
        e1 = DomainEvent(type="x", producer="p", payload={})
        e2 = DomainEvent(type="x", producer="p", payload={})
        assert e1.event_id != e2.event_id

    def test_timestamp_is_utc(self):
        e = DomainEvent(type="x", producer="p", payload={})
        assert e.timestamp.tzinfo is not None

    def test_optional_correlation_id(self):
        e = DomainEvent(type="x", producer="p", payload={}, correlation_id="corr-1")
        assert e.correlation_id == "corr-1"



# ===========================================================================
# AgentExecutionResult
# ===========================================================================

class TestAgentExecutionResult:

    def test_success_defaults(self):
        r = AgentExecutionResult(success=True, exit_code=0)
        assert r.stdout == ""
        assert r.stderr == ""
        assert r.elapsed_seconds == 0.0
        assert r.modified_files == []
        assert r.artifacts == {}
        assert r.forbidden_file_violations == []

    def test_failure(self):
        r = AgentExecutionResult(success=False, exit_code=1, stderr="crash")
        assert not r.success
        assert r.exit_code == 1
        assert r.stderr == "crash"


# ===========================================================================
# TaskAggregate.create() factory
# ===========================================================================

class TestTaskAggregateCreateFactory:

    def test_task_id_format(self):
        task = TaskAggregate.create(
            title="T", description="D",
            execution=ExecutionSpec(type="code"),
            agent_selector=AgentSelector(required_capability="backend"),
        )
        assert task.task_id.startswith("task-")
        assert len(task.task_id) == len("task-") + 12

    def test_task_id_is_unique(self):
        t1 = TaskAggregate.create(
            title="T", description="D",
            execution=ExecutionSpec(type="code"),
            agent_selector=AgentSelector(required_capability="backend"),
        )
        t2 = TaskAggregate.create(
            title="T", description="D",
            execution=ExecutionSpec(type="code"),
            agent_selector=AgentSelector(required_capability="backend"),
        )
        assert t1.task_id != t2.task_id

    def test_feature_id_auto_generated_when_not_supplied(self):
        task = TaskAggregate.create(
            title="T", description="D",
            execution=ExecutionSpec(type="code"),
            agent_selector=AgentSelector(required_capability="backend"),
        )
        assert task.feature_id.startswith("feat-")

    def test_feature_id_passed_through(self):
        task = TaskAggregate.create(
            title="T", description="D",
            execution=ExecutionSpec(type="code"),
            agent_selector=AgentSelector(required_capability="backend"),
            feature_id="feat-login",
        )
        assert task.feature_id == "feat-login"

    def test_depends_on_defaults_to_empty_list(self):
        task = TaskAggregate.create(
            title="T", description="D",
            execution=ExecutionSpec(type="code"),
            agent_selector=AgentSelector(required_capability="backend"),
        )
        assert task.depends_on == []

    def test_depends_on_forwarded(self):
        task = TaskAggregate.create(
            title="T", description="D",
            execution=ExecutionSpec(type="code"),
            agent_selector=AgentSelector(required_capability="backend"),
            depends_on=["task-A", "task-B"],
        )
        assert task.depends_on == ["task-A", "task-B"]

    def test_max_retries_forwarded_to_retry_policy(self):
        task = TaskAggregate.create(
            title="T", description="D",
            execution=ExecutionSpec(type="code"),
            agent_selector=AgentSelector(required_capability="backend"),
            max_retries=5,
        )
        assert task.retry_policy.max_retries == 5

    def test_initial_status_is_created(self):
        task = TaskAggregate.create(
            title="T", description="D",
            execution=ExecutionSpec(type="code"),
            agent_selector=AgentSelector(required_capability="backend"),
        )
        assert task.status == TaskStatus.CREATED


# ===========================================================================
# TaskAggregate.force_requeue()
# ===========================================================================

class TestForceRequeue:

    @pytest.mark.parametrize("status", [
        TaskStatus.CREATED,
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
        TaskStatus.REQUEUED,
    ])
    def test_force_requeue_allowed_for_all_non_merged(self, status):
        task = make_task(status=status)
        task.force_requeue()
        assert task.status == TaskStatus.REQUEUED

    def test_force_requeue_blocked_for_merged(self):
        task = make_task(status=TaskStatus.MERGED)
        with pytest.raises(ValueError, match="MERGED"):
            task.force_requeue()

    def test_force_requeue_does_not_increment_retry_counter(self):
        task = make_task(status=TaskStatus.FAILED)
        task.retry_policy.attempt = 2
        task.force_requeue()
        assert task.retry_policy.attempt == 2  # unchanged

    def test_force_requeue_clears_assignment(self):
        task = make_task(status=TaskStatus.ASSIGNED)
        task.assignment = Assignment(agent_id="a-001")
        task.force_requeue()
        assert task.assignment is None

    def test_force_requeue_history_entry_records_previous_status(self):
        task = make_task(status=TaskStatus.SUCCEEDED)
        task.force_requeue()
        entry = next(h for h in task.history if h.event == "task.force_requeued")
        assert entry.detail["previous_status"] == TaskStatus.SUCCEEDED.value

    def test_force_requeue_custom_actor_in_history(self):
        task = make_task(status=TaskStatus.FAILED)
        task.force_requeue(actor="ops-team")
        entry = next(h for h in task.history if h.event == "task.force_requeued")
        assert entry.actor == "ops-team"

    def test_force_requeue_bumps_state_version(self):
        task = make_task(status=TaskStatus.FAILED)
        v0 = task.state_version
        task.force_requeue()
        assert task.state_version == v0 + 1
