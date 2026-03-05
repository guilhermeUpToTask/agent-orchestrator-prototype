"""
tests/unit/test_domain.py — Unit tests for domain models and services.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.core.models import (
    AgentExecutionResult,
    AgentProps,
    AgentSelector,
    Assignment,
    ExecutionSpec,
    RetryPolicy,
    TaskAggregate,
    TaskResult,
    TaskStatus,
    TrustLevel,
)
from src.core.services import SchedulerService, LeaseService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_task(task_id: str = "task-001", status: TaskStatus = TaskStatus.CREATED) -> TaskAggregate:
    task = TaskAggregate(
        task_id=task_id,
        feature_id="feat-auth",
        title="Test task",
        description="A test",
        agent_selector=AgentSelector(required_capability="backend_dev"),
        execution=ExecutionSpec(
            type="code:backend",
            files_allowed_to_modify=["app/auth.py"],
            test_command="pytest tests/",
        ),
        status=status,
    )
    return task


def make_agent(
    agent_id: str = "agent-001",
    capabilities: list[str] | None = None,
    trust: TrustLevel = TrustLevel.MEDIUM,
) -> AgentProps:
    return AgentProps(
        agent_id=agent_id,
        name=f"Agent {agent_id}",
        capabilities=capabilities or ["backend_dev"],
        version="1.0.0",
        trust_level=trust,
    )


# ---------------------------------------------------------------------------
# TaskAggregate state machine
# ---------------------------------------------------------------------------

class TestTaskAggregate:

    def test_initial_status_is_created(self):
        task = make_task()
        assert task.status == TaskStatus.CREATED

    def test_assign_from_created(self):
        task = make_task()
        v0 = task.state_version
        assignment = Assignment(agent_id="agent-001")
        task.assign(assignment)
        assert task.status == TaskStatus.ASSIGNED
        assert task.state_version == v0 + 1
        assert len(task.history) == 1
        assert task.history[0].event == "task.assigned"

    def test_assign_from_requeued(self):
        task = make_task(status=TaskStatus.REQUEUED)
        task.assign(Assignment(agent_id="agent-001"))
        assert task.status == TaskStatus.ASSIGNED

    def test_assign_invalid_status_raises(self):
        task = make_task(status=TaskStatus.IN_PROGRESS)
        with pytest.raises(ValueError):
            task.assign(Assignment(agent_id="agent-001"))

    def test_full_happy_path(self):
        task = make_task()
        task.assign(Assignment(agent_id="agent-001"))
        task.start()
        assert task.status == TaskStatus.IN_PROGRESS

        result = TaskResult(branch="task/task-001", commit_sha="abc123")
        task.complete(result)
        assert task.status == TaskStatus.SUCCEEDED
        assert task.result.commit_sha == "abc123"

    def test_fail_then_requeue(self):
        task = make_task()
        task.assign(Assignment(agent_id="agent-001"))
        task.start()
        task.fail("timeout")
        assert task.status == TaskStatus.FAILED

        task.requeue()
        assert task.status == TaskStatus.REQUEUED
        assert task.retry_policy.attempt == 1

    def test_requeue_exceeds_max_retries(self):
        task = make_task()
        task.retry_policy.attempt = 2  # already at max
        task.assign(Assignment(agent_id="a"))
        task.start()
        task.fail("x")
        with pytest.raises(ValueError, match="max retries"):
            task.requeue()

    def test_state_version_increments_each_transition(self):
        task = make_task()
        assert task.state_version == 1
        task.assign(Assignment(agent_id="a"))
        assert task.state_version == 2
        task.start()
        assert task.state_version == 3
        task.complete(TaskResult())
        assert task.state_version == 4

    def test_cancel_any_status(self):
        task = make_task()
        task.cancel("user request")
        assert task.status == TaskStatus.CANCELED


# ---------------------------------------------------------------------------
# SchedulerService
# ---------------------------------------------------------------------------

class TestSchedulerService:

    def test_selects_agent_with_capability(self):
        task = make_task()
        agent = make_agent(capabilities=["backend_dev"])
        result = SchedulerService().select_agent(task, [agent])
        assert result is not None
        assert result.agent_id == "agent-001"

    def test_returns_none_if_no_match(self):
        task = make_task()
        agent = make_agent(capabilities=["frontend"])
        result = SchedulerService().select_agent(task, [agent])
        assert result is None

    def test_prefers_higher_trust(self):
        task = make_task()
        low = make_agent("low-trust", trust=TrustLevel.LOW)
        high = make_agent("high-trust", trust=TrustLevel.HIGH)
        result = SchedulerService().select_agent(task, [low, high])
        assert result.agent_id == "high-trust"

    def test_version_filter(self):
        task = make_task()
        task.agent_selector.min_version = ">=2.0.0"
        agent_old = make_agent("old")
        agent_old.version = "1.5.0"
        agent_new = make_agent("new")
        agent_new.version = "2.0.0"
        result = SchedulerService().select_agent(task, [agent_old, agent_new])
        assert result.agent_id == "new"


# ---------------------------------------------------------------------------
# LeaseService
# ---------------------------------------------------------------------------

class TestLeaseService:

    def test_should_requeue_assigned_expired(self):
        task = make_task(status=TaskStatus.ASSIGNED)
        task.assignment = Assignment(agent_id="a")
        assert LeaseService.should_requeue(task, lease_active=False) is True

    def test_no_requeue_if_lease_active(self):
        task = make_task(status=TaskStatus.ASSIGNED)
        task.assignment = Assignment(agent_id="a")
        assert LeaseService.should_requeue(task, lease_active=True) is False

    def test_should_fail_stale_in_progress(self):
        task = make_task(status=TaskStatus.IN_PROGRESS)
        task.assignment = Assignment(agent_id="a")
        assert LeaseService.should_fail_stale(task, lease_active=False) is True


# ---------------------------------------------------------------------------
# YamlTaskRepository
# ---------------------------------------------------------------------------

class TestYamlTaskRepository:

    def _make_repo(self, tmp_path: Path):
        from src.infra.fs.task_repository import YamlTaskRepository
        return YamlTaskRepository(tmp_path / "tasks")

    def test_save_and_load(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task()
        repo.save(task)
        loaded = repo.load(task.task_id)
        assert loaded.task_id == task.task_id
        assert loaded.status == TaskStatus.CREATED

    def test_update_if_version_success(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task()
        repo.save(task)

        task.assign(Assignment(agent_id="a"))
        ok = repo.update_if_version(task.task_id, task, expected_version=1)
        assert ok is True
        loaded = repo.load(task.task_id)
        assert loaded.status == TaskStatus.ASSIGNED

    def test_update_if_version_conflict(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task()
        repo.save(task)

        # Simulate concurrent update
        task.assign(Assignment(agent_id="a"))
        ok = repo.update_if_version(task.task_id, task, expected_version=99)
        assert ok is False

    def test_list_all(self, tmp_path):
        repo = self._make_repo(tmp_path)
        for i in range(3):
            repo.save(make_task(f"task-00{i}"))
        tasks = repo.list_all()
        assert len(tasks) == 3

    def test_load_missing_raises(self, tmp_path):
        repo = self._make_repo(tmp_path)
        with pytest.raises(KeyError):
            repo.load("nonexistent")
