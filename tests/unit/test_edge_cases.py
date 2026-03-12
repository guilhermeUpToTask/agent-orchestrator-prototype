"""
tests/unit/test_edge_cases.py — Edge cases, boundary conditions, adversarial inputs.

Covers:
  - Concurrent task assignment (optimistic locking semantics)
  - Tasks with empty / unusual field values
  - Very large history lists
  - Scheduler with 0, 1, N agents in various states
  - Lease boundary timing (just-expired leases)
  - YamlTaskRepository concurrent write simulation
  - DomainEvent payload validation
  - Agent with empty capabilities list
  - RetryPolicy attempt boundary arithmetic
  - State transition chains not fully initialized
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from src.core.models import (
    AgentProps,
    AgentSelector,
    Assignment,
    DomainEvent,
    ExecutionSpec,
    HistoryEntry,
    RetryPolicy,
    TaskAggregate,
    TaskResult,
    TaskStatus,
    TrustLevel,
)
from src.core.services import LeaseService, SchedulerService, _is_alive


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_task(task_id: str = "task-001", **kwargs) -> TaskAggregate:
    defaults = dict(
        task_id=task_id,
        feature_id="feat-x",
        title="T",
        description="D",
        agent_selector=AgentSelector(required_capability="backend_dev"),
        execution=ExecutionSpec(type="code:backend"),
        status=TaskStatus.CREATED,
    )
    defaults.update(kwargs)
    return TaskAggregate(**defaults)


def alive_agent(agent_id: str = "a-001", capabilities: list[str] | None = None) -> AgentProps:
    return AgentProps(
        agent_id=agent_id,
        name=agent_id,
        capabilities=capabilities or ["backend_dev"],
        version="1.0.0",
        last_heartbeat=datetime.now(timezone.utc),
    )


# ===========================================================================
# TaskAggregate — boundary conditions
# ===========================================================================

class TestTaskAggregateBoundaries:

    def test_task_id_with_special_characters(self):
        """task_id is used as a filename — this tests model acceptance."""
        task = make_task("task-abc-001_XYZ")
        assert task.task_id == "task-abc-001_XYZ"

    def test_very_long_description(self):
        task = make_task(description="A" * 10_000)
        assert len(task.description) == 10_000

    def test_empty_title_accepted(self):
        task = make_task(title="")
        assert task.title == ""

    def test_depends_on_with_many_entries(self):
        deps = [f"task-{i:04d}" for i in range(100)]
        task = make_task(depends_on=deps)
        assert len(task.depends_on) == 100

    def test_history_grows_unbounded(self):
        task = make_task()
        for i in range(50):
            task._bump(f"event.{i}", "system")
        assert len(task.history) == 50

    def test_state_version_monotonically_increases(self):
        task = make_task()
        versions = [task.state_version]
        task.assign(Assignment(agent_id="a"))
        versions.append(task.state_version)
        task.start()
        versions.append(task.state_version)
        task.complete(TaskResult())
        versions.append(task.state_version)
        assert versions == sorted(versions)
        assert len(set(versions)) == len(versions)  # strictly increasing

    def test_retry_policy_attempt_zero_not_exceeded(self):
        rp = RetryPolicy(max_retries=2, attempt=0)
        task = make_task(retry_policy=rp)
        task.assign(Assignment(agent_id="a"))
        task.start()
        task.fail("x")
        task.requeue()  # attempt becomes 1
        assert task.retry_policy.attempt == 1

    def test_max_retries_zero_means_no_requeue(self):
        task = make_task(retry_policy=RetryPolicy(max_retries=0, attempt=0))
        task.assign(Assignment(agent_id="a"))
        task.start()
        task.fail("x")
        with pytest.raises(ValueError, match="max retries"):
            task.requeue()

    def test_cancel_from_succeeded_is_allowed(self):
        task = make_task()
        task.assign(Assignment(agent_id="a"))
        task.start()
        task.complete(TaskResult())
        task.cancel("admin override")
        assert task.status == TaskStatus.CANCELED

    def test_assignment_lease_token_stored(self):
        task = make_task()
        assignment = Assignment(agent_id="a", lease_token="tok-xyz")
        task.assign(assignment)
        assert task.assignment.lease_token == "tok-xyz"

    def test_result_with_artifacts(self):
        task = make_task()
        task.assign(Assignment(agent_id="a"))
        task.start()
        result = TaskResult(
            branch="task/t1",
            commit_sha="sha1",
            modified_files=["a.py", "b.py"],
            artifacts={"coverage": 0.95, "lint_errors": 0},
        )
        task.complete(result)
        assert task.result.artifacts["coverage"] == 0.95


# ===========================================================================
# Scheduler — edge cases
# ===========================================================================

class TestSchedulerEdgeCases:
    svc = SchedulerService()

    def test_all_agents_inactive(self):
        task = make_task()
        agents = [
            AgentProps(
                agent_id=f"a-{i}", name="n",
                capabilities=["backend_dev"], version="1.0.0",
                active=False, last_heartbeat=datetime.now(timezone.utc),
            )
            for i in range(5)
        ]
        assert self.svc.select_agent(task, agents) is None

    def test_all_agents_dead(self):
        task = make_task()
        agents = [
            AgentProps(
                agent_id=f"a-{i}", name="n",
                capabilities=["backend_dev"], version="1.0.0",
                last_heartbeat=datetime.now(timezone.utc) - timedelta(hours=1),
            )
            for i in range(5)
        ]
        assert self.svc.select_agent(task, agents) is None

    def test_exactly_one_eligible_among_many_ineligible(self):
        task = make_task()
        agents = [
            AgentProps(
                agent_id=f"a-{i}", name="n",
                capabilities=["frontend"], version="1.0.0",
                last_heartbeat=datetime.now(timezone.utc),
            )
            for i in range(10)
        ]
        eligible = alive_agent("winner", capabilities=["backend_dev"])
        agents.append(eligible)
        result = self.svc.select_agent(task, agents)
        assert result is not None
        assert result.agent_id == "winner"

    def test_version_constraint_exact_boundary(self):
        """Agent at exactly the required version should pass."""
        task = make_task()
        task.agent_selector.min_version = ">=1.5.3"
        agent = alive_agent()
        agent.version = "1.5.3"
        result = self.svc.select_agent(task, [agent])
        assert result is not None

    def test_version_just_below_constraint_excluded(self):
        task = make_task()
        task.agent_selector.min_version = ">=1.5.3"
        agent = alive_agent()
        agent.version = "1.5.2"
        result = self.svc.select_agent(task, [agent])
        assert result is None

    def test_empty_capabilities_agent_excluded(self):
        task = make_task()
        agent = AgentProps(
            agent_id="empty-caps", name="n",
            capabilities=[],  # no capabilities
            version="1.0.0",
            last_heartbeat=datetime.now(timezone.utc),
        )
        result = self.svc.select_agent(task, [agent])
        assert result is None

    def test_agent_with_extra_capabilities_eligible(self):
        task = make_task()
        agent = alive_agent(capabilities=["backend_dev", "ml", "frontend"])
        result = self.svc.select_agent(task, [agent])
        assert result is not None

    def test_multiple_identical_score_returns_one(self):
        """When all agents have identical score, select_agent should return exactly one."""
        task = make_task()
        agents = [alive_agent(f"a-{i}") for i in range(5)]
        result = self.svc.select_agent(task, agents)
        assert result is not None
        assert result.agent_id in [f"a-{i}" for i in range(5)]


# ===========================================================================
# LeaseService — boundary
# ===========================================================================

class TestLeaseServiceBoundary:

    def test_should_requeue_at_zero_attempts(self):
        task = make_task(status=TaskStatus.ASSIGNED, retry_policy=RetryPolicy(max_retries=1, attempt=0))
        task.assignment = Assignment(agent_id="a")
        assert LeaseService.should_requeue(task, lease_active=False) is True

    def test_should_not_requeue_at_max_retries_boundary(self):
        # attempt == max_retries → NOT eligible
        task = make_task(status=TaskStatus.ASSIGNED, retry_policy=RetryPolicy(max_retries=3, attempt=3))
        task.assignment = Assignment(agent_id="a")
        assert LeaseService.should_requeue(task, lease_active=False) is False

    def test_should_not_fail_stale_for_succeeded(self):
        task = make_task(status=TaskStatus.SUCCEEDED)
        assert LeaseService.should_fail_stale(task, lease_active=False) is False

    def test_should_not_fail_stale_for_canceled(self):
        task = make_task(status=TaskStatus.CANCELED)
        assert LeaseService.should_fail_stale(task, lease_active=False) is False


# ===========================================================================
# _is_alive — edge cases
# ===========================================================================

class TestIsAliveEdgeCases:

    def test_future_heartbeat_is_alive(self):
        """An agent with heartbeat in the future (clock skew) is considered alive."""
        agent = AgentProps(
            agent_id="a", name="n",
            last_heartbeat=datetime.now(timezone.utc) + timedelta(seconds=5),
        )
        assert _is_alive(agent) is True

    def test_heartbeat_exactly_at_threshold_is_dead(self):
        agent = AgentProps(
            agent_id="a", name="n",
            last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        # age == 60s, threshold == 60 → age < threshold is False → dead
        assert _is_alive(agent, threshold_seconds=60) is False


# ===========================================================================
# YamlTaskRepository — edge cases
# ===========================================================================

class TestYamlRepoEdgeCases:

    def _make_repo(self, tmp_path: Path):
        from src.infra.fs.task_repository import YamlTaskRepository
        return YamlTaskRepository(tmp_path / "tasks")

    def test_directory_created_if_not_exists(self, tmp_path):
        repo = self._make_repo(tmp_path)
        assert (tmp_path / "tasks").exists()

    def test_save_task_with_full_history(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task()
        task.assign(Assignment(agent_id="a"))
        task.start()
        task.complete(TaskResult(commit_sha="abc"))
        repo.save(task)
        loaded = repo.load(task.task_id)
        assert len(loaded.history) == 3

    def test_load_round_trip_preserves_datetime(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task()
        repo.save(task)
        loaded = repo.load(task.task_id)
        # created_at should round-trip through YAML/Pydantic
        assert abs((loaded.created_at - task.created_at).total_seconds()) < 1

    def test_update_if_version_correct_version_zero(self, tmp_path):
        """
        state_version starts at 1. Passing expected_version=0 should fail.
        """
        repo = self._make_repo(tmp_path)
        task = make_task()
        repo.save(task)
        task.assign(Assignment(agent_id="a"))
        ok = repo.update_if_version(task.task_id, task, expected_version=0)
        assert ok is False

    def test_list_all_sorted_by_filename(self, tmp_path):
        repo = self._make_repo(tmp_path)
        for tid in ["task-c", "task-a", "task-b"]:
            repo.save(make_task(tid))
        tasks = repo.list_all()
        ids = [t.task_id for t in tasks]
        assert ids == sorted(ids)

    def test_save_and_reload_depends_on(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task(depends_on=["dep-1", "dep-2", "dep-3"])
        repo.save(task)
        loaded = repo.load(task.task_id)
        assert loaded.depends_on == ["dep-1", "dep-2", "dep-3"]


# ===========================================================================
# InMemoryLeaseAdapter — edge cases
# ===========================================================================

class TestLeaseAdapterEdgeCases:

    def _make(self):
        from src.infra.redis_adapters.lease_memory import InMemoryLeaseAdapter
        return InMemoryLeaseAdapter()

    def test_creating_lease_for_same_task_twice_replaces(self):
        adapter = self._make()
        t1 = adapter.create_lease("task-1", "agent-A", 300)
        t2 = adapter.create_lease("task-1", "agent-B", 300)
        assert adapter.get_lease_agent("task-1") == "agent-B"
        # Old token should no longer work
        assert adapter.revoke_lease(t1) is False

    def test_zero_second_lease_expires_immediately(self):
        adapter = self._make()
        adapter.create_lease("task-0", "agent-1", 0)
        # With 0 seconds it's immediately expired (monotonic comparison)
        import time; time.sleep(0.01)
        assert adapter.is_lease_active("task-0") is False

    def test_revoke_cleans_both_indices(self):
        adapter = self._make()
        token = adapter.create_lease("task-1", "agent-1", 300)
        adapter.revoke_lease(token)
        # Both _leases and _tokens should be cleaned up
        assert adapter.is_lease_active("task-1") is False
        assert adapter.get_lease_agent("task-1") is None


# ===========================================================================
# InMemoryEventAdapter — edge cases
# ===========================================================================

class TestEventAdapterEdgeCases:

    def _make(self):
        from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter
        return InMemoryEventAdapter()

    def test_subscribe_empty_store_returns_nothing(self):
        adapter = self._make()
        events = list(adapter.subscribe("task.created"))
        assert events == []

    def test_subscribe_many_with_empty_types(self):
        adapter = self._make()
        events = list(adapter.subscribe_many([]))
        assert events == []

    def test_publish_stores_event_reference(self):
        adapter = self._make()
        evt = DomainEvent(type="task.created", producer="p", payload={"task_id": "t"})
        adapter.publish(evt)
        assert adapter.all_events[0] is evt  # same object

    def test_multiple_event_types_independent(self):
        adapter = self._make()
        for t in ["task.created", "task.assigned", "task.completed"]:
            adapter.publish(DomainEvent(type=t, producer="p", payload={}))
        assert len(adapter.events_of_type("task.created")) == 1
        assert len(adapter.events_of_type("task.assigned")) == 1
        assert len(adapter.events_of_type("task.completed")) == 1


# ===========================================================================
# DomainEvent — validation
# ===========================================================================

class TestDomainEventEdgeCases:

    def test_empty_payload_is_valid(self):
        evt = DomainEvent(type="task.created", producer="test", payload={})
        assert evt.payload == {}

    def test_nested_payload_preserved(self):
        payload = {"task_id": "t1", "meta": {"key": "val", "num": 42}}
        evt = DomainEvent(type="task.created", producer="test", payload=payload)
        assert evt.payload["meta"]["key"] == "val"

    def test_correlation_and_causation_ids(self):
        evt = DomainEvent(
            type="task.created",
            producer="test",
            payload={},
            correlation_id="corr-123",
            causation_id="cause-456",
        )
        assert evt.correlation_id == "corr-123"
        assert evt.causation_id == "cause-456"

    def test_metadata_defaults_empty(self):
        evt = DomainEvent(type="x", producer="p", payload={})
        assert evt.metadata == {}


# ===========================================================================
# Full multi-task dependency chain
# ===========================================================================

class TestMultiTaskDependencyChain:
    """
    Simulates a 3-task chain: A → B → C.
    Verifies that B only starts after A succeeds, and C only after B.
    """

    def test_three_task_chain(self, tmp_path):
        from src.infra.fs.task_repository import YamlTaskRepository
        from src.infra.fs.agent_registry import JsonAgentRegistry
        from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter
        from src.infra.redis_adapters.lease_memory import InMemoryLeaseAdapter
        from src.app.handlers.task_manager import TaskManagerHandler

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        task_repo = YamlTaskRepository(tasks_dir)
        agent_registry = JsonAgentRegistry(agents_dir / "registry.json")
        event_port = InMemoryEventAdapter()
        lease_port = InMemoryLeaseAdapter()

        agent = alive_agent()
        agent_registry.register(agent)

        task_a = make_task("task-A")
        task_b = make_task("task-B", depends_on=["task-A"])
        task_c = make_task("task-C", depends_on=["task-B"])

        for t in [task_a, task_b, task_c]:
            task_repo.save(t)

        tm = TaskManagerHandler(
            task_repo=task_repo,
            agent_registry=agent_registry,
            event_port=event_port,
            lease_port=lease_port,
        )

        # B and C should not be assignable yet
        assert tm.handle_task_created("task-B") is False
        assert tm.handle_task_created("task-C") is False

        # A can be assigned
        assert tm.handle_task_created("task-A") is True

        # Simulate A completing
        a = task_repo.load("task-A")
        a.start()
        a.complete(TaskResult(commit_sha="sha-a"))
        task_repo.save(a)

        # Now B should be unblockable
        tm.handle_task_completed("task-A")
        b = task_repo.load("task-B")
        assert b.status == TaskStatus.ASSIGNED

        # C still blocked
        assert task_repo.load("task-C").status == TaskStatus.CREATED

        # Simulate B completing
        b.start()
        b.complete(TaskResult(commit_sha="sha-b"))
        task_repo.save(b)
        tm.handle_task_completed("task-B")

        c = task_repo.load("task-C")
        assert c.status == TaskStatus.ASSIGNED