"""
tests/integration/test_task_manager.py — Integration tests for TaskManagerHandler.

Covers:
  - handle_task_created: happy path, no eligible agent, already assigned
  - handle_task_requeued: assigns correctly
  - handle_task_completed: unblocks dependents, partial deps, no-op
  - Optimistic concurrency / version conflict retry
  - depends_on: single dep, multiple deps, partially satisfied
  - Lease is created on successful assignment
  - Event emitted after successful assignment
  - Task not in correct state is skipped
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from src.core.models import (
    AgentProps,
    AgentSelector,
    Assignment,
    ExecutionSpec,
    RetryPolicy,
    TaskAggregate,
    TaskStatus,
    TrustLevel,
    DomainEvent,
)
from src.core.services import SchedulerService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_workflow(tmp_path):
    (tmp_path / "tasks").mkdir()
    (tmp_path / "agents").mkdir()
    return tmp_path


@pytest.fixture()
def task_repo(tmp_workflow):
    from src.infra.fs.task_repository import YamlTaskRepository
    return YamlTaskRepository(tmp_workflow / "tasks")


@pytest.fixture()
def agent_registry(tmp_workflow):
    from src.infra.fs.agent_registry import JsonAgentRegistry
    return JsonAgentRegistry(tmp_workflow / "agents" / "registry.json")


@pytest.fixture()
def event_port():
    from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter
    return InMemoryEventAdapter()


@pytest.fixture()
def lease_port():
    from src.infra.redis_adapters.lease_memory import InMemoryLeaseAdapter
    return InMemoryLeaseAdapter()


@pytest.fixture()
def worker_agent(agent_registry) -> AgentProps:
    agent = AgentProps(
        agent_id="agent-worker-001",
        name="Worker 001",
        capabilities=["backend_dev"],
        version="1.2.0",
        tools=["pytest", "git"],
        trust_level=TrustLevel.HIGH,
        last_heartbeat=datetime.now(timezone.utc),
    )
    agent_registry.register(agent)
    return agent


def make_task(
    task_id: str = "task-001",
    required_cap: str = "backend_dev",
    depends_on: list[str] | None = None,
    status: TaskStatus = TaskStatus.CREATED,
) -> TaskAggregate:
    return TaskAggregate(
        task_id=task_id,
        feature_id="feat-x",
        title=f"Task {task_id}",
        description="Desc",
        agent_selector=AgentSelector(required_capability=required_cap),
        execution=ExecutionSpec(type="code:backend"),
        status=status,
        depends_on=depends_on or [],
    )


def build_task_manager(task_repo, agent_registry, event_port, lease_port):
    from src.app.handlers.task_manager import TaskManagerHandler
    return TaskManagerHandler(
        task_repo=task_repo,
        agent_registry=agent_registry,
        event_port=event_port,
        lease_port=lease_port,
        scheduler=SchedulerService(),
    )


# ===========================================================================
# handle_task_created
# ===========================================================================

class TestHandleTaskCreated:

    def test_happy_path_returns_true(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        task = make_task()
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_created(task.task_id)
        assert result is True

    def test_task_transitions_to_assigned(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        task = make_task()
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        tm.handle_task_created(task.task_id)
        loaded = task_repo.load(task.task_id)
        assert loaded.status == TaskStatus.ASSIGNED

    def test_assignment_has_correct_agent(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        task = make_task()
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        tm.handle_task_created(task.task_id)
        loaded = task_repo.load(task.task_id)
        assert loaded.assignment.agent_id == worker_agent.agent_id

    def test_emits_task_assigned_event(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        task = make_task()
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        tm.handle_task_created(task.task_id)
        events = event_port.events_of_type("task.assigned")
        assert len(events) == 1
        assert events[0].payload["task_id"] == task.task_id

    def test_event_contains_agent_id(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        task = make_task()
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        tm.handle_task_created(task.task_id)
        evt = event_port.events_of_type("task.assigned")[0]
        assert evt.payload["agent_id"] == worker_agent.agent_id

    def test_creates_lease_for_assigned_task(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        task = make_task()
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        tm.handle_task_created(task.task_id)
        assert lease_port.is_lease_active(task.task_id)

    def test_no_eligible_agent_returns_false(self, task_repo, agent_registry, event_port, lease_port):
        # No agents registered
        task = make_task()
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_created(task.task_id)
        assert result is False

    def test_no_eligible_agent_leaves_task_created(self, task_repo, agent_registry, event_port, lease_port):
        task = make_task()
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        tm.handle_task_created(task.task_id)
        loaded = task_repo.load(task.task_id)
        assert loaded.status == TaskStatus.CREATED

    def test_wrong_capability_agent_returns_false(self, task_repo, agent_registry, event_port, lease_port):
        # Register an agent with wrong capability
        frontend_agent = AgentProps(
            agent_id="frontend-agent",
            name="Frontend",
            capabilities=["frontend"],
            version="1.0.0",
            last_heartbeat=datetime.now(timezone.utc),
        )
        agent_registry.register(frontend_agent)
        task = make_task(required_cap="backend_dev")
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_created(task.task_id)
        assert result is False

    def test_already_assigned_task_is_skipped(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        task = make_task(status=TaskStatus.ASSIGNED)
        task.assignment = Assignment(agent_id="other-agent")
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_created(task.task_id)
        assert result is False
        # No new events
        assert event_port.events_of_type("task.assigned") == []

    def test_in_progress_task_is_skipped(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        task = make_task(status=TaskStatus.IN_PROGRESS)
        task.assignment = Assignment(agent_id=worker_agent.agent_id)
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_created(task.task_id)
        assert result is False

    def test_inactive_agent_not_selected(self, task_repo, agent_registry, event_port, lease_port):
        inactive_agent = AgentProps(
            agent_id="inactive-agent",
            name="Inactive",
            capabilities=["backend_dev"],
            version="1.0.0",
            active=False,
            last_heartbeat=datetime.now(timezone.utc),
        )
        agent_registry.register(inactive_agent)
        task = make_task()
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_created(task.task_id)
        assert result is False

    def test_dead_agent_not_selected(self, task_repo, agent_registry, event_port, lease_port):
        dead_agent = AgentProps(
            agent_id="dead-agent",
            name="Dead",
            capabilities=["backend_dev"],
            version="1.0.0",
            last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=300),
        )
        agent_registry.register(dead_agent)
        task = make_task()
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_created(task.task_id)
        assert result is False


# ===========================================================================
# handle_task_requeued
# ===========================================================================

class TestHandleTaskRequeued:

    def test_requeued_task_gets_assigned(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        task = make_task(status=TaskStatus.REQUEUED)
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_requeued(task.task_id)
        assert result is True
        loaded = task_repo.load(task.task_id)
        assert loaded.status == TaskStatus.ASSIGNED

    def test_requeued_emits_assigned_event(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        task = make_task(status=TaskStatus.REQUEUED)
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        tm.handle_task_requeued(task.task_id)
        assert len(event_port.events_of_type("task.assigned")) == 1


# ===========================================================================
# depends_on / dependency graph
# ===========================================================================

class TestDependencyResolution:

    def test_task_with_unmet_dep_not_assigned(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        dep_task = make_task("task-dep")
        task_repo.save(dep_task)  # CREATED, not SUCCEEDED

        dependent = make_task("task-dependent", depends_on=["task-dep"])
        task_repo.save(dependent)

        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_created("task-dependent")
        assert result is False
        loaded = task_repo.load("task-dependent")
        assert loaded.status == TaskStatus.CREATED

    def test_task_with_met_dep_gets_assigned(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        # Create a succeeded dependency
        dep_task = make_task("task-dep", status=TaskStatus.SUCCEEDED)
        task_repo.save(dep_task)

        dependent = make_task("task-dependent", depends_on=["task-dep"])
        task_repo.save(dependent)

        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_created("task-dependent")
        assert result is True
        loaded = task_repo.load("task-dependent")
        assert loaded.status == TaskStatus.ASSIGNED

    def test_multiple_deps_all_must_succeed(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        dep1 = make_task("dep-1", status=TaskStatus.SUCCEEDED)
        dep2 = make_task("dep-2", status=TaskStatus.CREATED)  # not done
        task_repo.save(dep1)
        task_repo.save(dep2)

        dependent = make_task("task-dependent", depends_on=["dep-1", "dep-2"])
        task_repo.save(dependent)

        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_created("task-dependent")
        assert result is False

    def test_all_deps_succeeded_unblocks_task(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        dep1 = make_task("dep-1", status=TaskStatus.SUCCEEDED)
        dep2 = make_task("dep-2", status=TaskStatus.SUCCEEDED)
        task_repo.save(dep1)
        task_repo.save(dep2)

        dependent = make_task("task-dependent", depends_on=["dep-1", "dep-2"])
        task_repo.save(dependent)

        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_created("task-dependent")
        assert result is True

    def test_no_deps_assigns_immediately(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        task = make_task("no-deps")
        task_repo.save(task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_created("no-deps")
        assert result is True


# ===========================================================================
# handle_task_completed — dependency unblocking
# ===========================================================================

class TestHandleTaskCompleted:

    def test_completes_and_unblocks_dependent(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        # Mark dep as succeeded
        dep = make_task("task-dep", status=TaskStatus.SUCCEEDED)
        task_repo.save(dep)

        # Dependent waiting for dep
        dependent = make_task("task-dependent", depends_on=["task-dep"])
        task_repo.save(dependent)

        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        tm.handle_task_completed("task-dep")

        loaded = task_repo.load("task-dependent")
        assert loaded.status == TaskStatus.ASSIGNED

    def test_does_not_unblock_with_partial_deps(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        dep1 = make_task("dep-1", status=TaskStatus.SUCCEEDED)
        dep2 = make_task("dep-2", status=TaskStatus.CREATED)
        task_repo.save(dep1)
        task_repo.save(dep2)

        dependent = make_task("task-dependent", depends_on=["dep-1", "dep-2"])
        task_repo.save(dependent)

        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        tm.handle_task_completed("dep-1")

        loaded = task_repo.load("task-dependent")
        assert loaded.status == TaskStatus.CREATED  # still blocked

    def test_completing_irrelevant_task_does_nothing(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        task = make_task("task-standalone")
        task_repo.save(task)

        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        tm.handle_task_completed("some-unrelated-task-id")

        loaded = task_repo.load("task-standalone")
        assert loaded.status == TaskStatus.CREATED

    def test_already_assigned_dependent_not_re_assigned(self, task_repo, agent_registry, event_port, lease_port, worker_agent):
        dep = make_task("dep-1", status=TaskStatus.SUCCEEDED)
        task_repo.save(dep)

        dependent = make_task("task-dep", depends_on=["dep-1"], status=TaskStatus.ASSIGNED)
        dependent.assignment = Assignment(agent_id=worker_agent.agent_id)
        task_repo.save(dependent)

        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        tm.handle_task_completed("dep-1")

        # Still only one assign event (possibly zero if handle_task_completed
        # skips non-CREATED dependents)
        loaded = task_repo.load("task-dep")
        assert loaded.status == TaskStatus.ASSIGNED  # unchanged


# ===========================================================================
# Concurrent assignment / version conflict (simulated)
# ===========================================================================

class TestVersionConflict:

    def test_retries_on_version_conflict(self, tmp_path, event_port, lease_port, worker_agent):
        """
        If update_if_version fails the first time, TaskManagerHandler retries.
        We simulate this by patching the repo to fail once then succeed.
        """
        from src.infra.fs.task_repository import YamlTaskRepository
        from src.infra.fs.agent_registry import JsonAgentRegistry
        from src.app.handlers.task_manager import TaskManagerHandler

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        task_repo = YamlTaskRepository(tasks_dir)
        agent_registry = JsonAgentRegistry(agents_dir / "registry.json")
        agent_registry.register(worker_agent)

        task = make_task()
        task_repo.save(task)

        call_count = {"n": 0}
        original_update = task_repo.update_if_version

        def patched_update(task_id, new_state, expected_version):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return False  # simulate conflict on first call
            return original_update(task_id, new_state, expected_version)

        task_repo.update_if_version = patched_update

        tm = TaskManagerHandler(
            task_repo=task_repo,
            agent_registry=agent_registry,
            event_port=event_port,
            lease_port=lease_port,
            scheduler=SchedulerService(),
        )
        result = tm.handle_task_created(task.task_id)
        assert result is True
        assert call_count["n"] >= 2  # retried at least once