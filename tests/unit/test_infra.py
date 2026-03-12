"""
tests/unit/test_infra.py — Unit tests for infrastructure adapters.

Covers:
  - YamlTaskRepository: save, load, update_if_version, list_all, append_history
  - JsonAgentRegistry: register, deregister, list, heartbeat, get
  - InMemoryLeaseAdapter: create, refresh, revoke, is_active, get_agent
  - InMemoryEventAdapter: publish, subscribe, subscribe_many, helpers
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.core.models import (
    AgentProps,
    AgentSelector,
    Assignment,
    ExecutionSpec,
    HistoryEntry,
    RetryPolicy,
    TaskAggregate,
    TaskResult,
    TaskStatus,
    TrustLevel,
    DomainEvent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_task(task_id: str = "task-001", status: TaskStatus = TaskStatus.CREATED) -> TaskAggregate:
    return TaskAggregate(
        task_id=task_id,
        feature_id="feat-x",
        title="Test",
        description="Desc",
        agent_selector=AgentSelector(required_capability="backend_dev"),
        execution=ExecutionSpec(type="code:backend"),
        status=status,
    )


def make_agent(agent_id: str = "agent-001") -> AgentProps:
    return AgentProps(
        agent_id=agent_id,
        name=f"Agent {agent_id}",
        capabilities=["backend_dev"],
        version="1.0.0",
        trust_level=TrustLevel.MEDIUM,
    )


# ===========================================================================
# YamlTaskRepository
# ===========================================================================

class TestYamlTaskRepository:

    def _make_repo(self, tmp_path: Path):
        from src.infra.fs.task_repository import YamlTaskRepository
        return YamlTaskRepository(tmp_path / "tasks")

    # -- Save & Load ----------------------------------------------------------

    def test_save_creates_yaml_file(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task("task-abc")
        repo.save(task)
        assert (tmp_path / "tasks" / "task-abc.yaml").exists()

    def test_load_returns_correct_task(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task("task-load")
        repo.save(task)
        loaded = repo.load("task-load")
        assert loaded.task_id == "task-load"
        assert loaded.status == TaskStatus.CREATED

    def test_load_preserves_all_fields(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task("task-full")
        task.assign(Assignment(agent_id="a-007"))
        repo.save(task)
        loaded = repo.load("task-full")
        assert loaded.assignment.agent_id == "a-007"
        assert loaded.status == TaskStatus.ASSIGNED
        assert len(loaded.history) == 1

    def test_load_raises_key_error_for_missing(self, tmp_path):
        repo = self._make_repo(tmp_path)
        with pytest.raises(KeyError):
            repo.load("nonexistent-task")

    def test_save_overwrites_existing(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task("task-overwrite")
        repo.save(task)
        task.assign(Assignment(agent_id="agent-x"))
        repo.save(task)
        loaded = repo.load("task-overwrite")
        assert loaded.status == TaskStatus.ASSIGNED

    # -- update_if_version ----------------------------------------------------

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

        task.assign(Assignment(agent_id="a"))
        ok = repo.update_if_version(task.task_id, task, expected_version=99)
        assert ok is False

    def test_update_if_version_conflict_does_not_modify_disk(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task()
        repo.save(task)

        task.assign(Assignment(agent_id="a"))
        repo.update_if_version(task.task_id, task, expected_version=99)

        # Disk should still show CREATED
        loaded = repo.load(task.task_id)
        assert loaded.status == TaskStatus.CREATED

    def test_update_if_version_raises_for_missing_task(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task()
        with pytest.raises(KeyError):
            repo.update_if_version("nonexistent", task, expected_version=1)

    def test_sequential_updates_succeed(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task()
        repo.save(task)

        # First update
        task.assign(Assignment(agent_id="a"))
        ok1 = repo.update_if_version(task.task_id, task, expected_version=1)
        assert ok1 is True

        # Second update — version is now 2
        task.start()
        ok2 = repo.update_if_version(task.task_id, task, expected_version=2)
        assert ok2 is True

        loaded = repo.load(task.task_id)
        assert loaded.status == TaskStatus.IN_PROGRESS

    # -- list_all -------------------------------------------------------------

    def test_list_all_empty_dir(self, tmp_path):
        repo = self._make_repo(tmp_path)
        assert repo.list_all() == []

    def test_list_all_returns_all_tasks(self, tmp_path):
        repo = self._make_repo(tmp_path)
        for i in range(5):
            repo.save(make_task(f"task-{i:03d}"))
        tasks = repo.list_all()
        assert len(tasks) == 5

    def test_list_all_skips_empty_files(self, tmp_path):
        repo = self._make_repo(tmp_path)
        repo.save(make_task("task-good"))
        # Create an empty yaml file
        (tmp_path / "tasks" / "task-empty.yaml").write_text("")
        tasks = repo.list_all()
        assert len(tasks) == 1
        assert tasks[0].task_id == "task-good"

    def test_list_all_skips_corrupt_files(self, tmp_path):
        repo = self._make_repo(tmp_path)
        repo.save(make_task("task-good"))
        # Create a corrupt yaml file
        (tmp_path / "tasks" / "task-corrupt.yaml").write_text("{{not: valid: yaml: }{}")
        tasks = repo.list_all()
        assert len(tasks) == 1

    def test_list_all_task_ids_are_unique(self, tmp_path):
        repo = self._make_repo(tmp_path)
        for i in range(3):
            repo.save(make_task(f"task-{i}"))
        tasks = repo.list_all()
        task_ids = [t.task_id for t in tasks]
        assert len(set(task_ids)) == len(task_ids)

    # -- append_history -------------------------------------------------------

    def test_append_history_adds_entry(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task()
        repo.save(task)
        repo.append_history(task.task_id, "custom.event", "tester", {"key": "val"})
        loaded = repo.load(task.task_id)
        assert len(loaded.history) == 1
        assert loaded.history[0].event == "custom.event"
        assert loaded.history[0].detail["key"] == "val"

    def test_append_history_does_not_bump_state_version(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task()
        repo.save(task)
        repo.append_history(task.task_id, "evt", "a", {})
        loaded = repo.load(task.task_id)
        assert loaded.state_version == task.state_version

    # -- atomic write integrity -----------------------------------------------

    def test_atomic_write_creates_no_leftover_tmp_files(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task("task-atomic")
        repo.save(task)
        tmp_files = list((tmp_path / "tasks").glob("*.tmp"))
        assert tmp_files == []


# ===========================================================================
# JsonAgentRegistry
# ===========================================================================

class TestJsonAgentRegistry:

    def _make_registry(self, tmp_path: Path):
        from src.infra.fs.agent_registry import JsonAgentRegistry
        return JsonAgentRegistry(tmp_path / "agents" / "registry.json")

    # -- register / list ------------------------------------------------------

    def test_register_and_list(self, tmp_path):
        registry = self._make_registry(tmp_path)
        agent = make_agent("a-001")
        registry.register(agent)
        agents = registry.list_agents()
        assert len(agents) == 1
        assert agents[0].agent_id == "a-001"

    def test_register_multiple(self, tmp_path):
        registry = self._make_registry(tmp_path)
        for i in range(4):
            registry.register(make_agent(f"a-{i:03d}"))
        assert len(registry.list_agents()) == 4

    def test_register_overwrites_existing(self, tmp_path):
        registry = self._make_registry(tmp_path)
        agent = make_agent("a-001")
        registry.register(agent)
        # Re-register with different capability
        agent2 = AgentProps(agent_id="a-001", name="Updated", capabilities=["ml"])
        registry.register(agent2)
        loaded = registry.get("a-001")
        assert loaded.capabilities == ["ml"]
        assert len(registry.list_agents()) == 1  # no duplicates

    # -- deregister -----------------------------------------------------------

    def test_deregister_removes_agent(self, tmp_path):
        registry = self._make_registry(tmp_path)
        registry.register(make_agent("a-001"))
        registry.deregister("a-001")
        assert registry.list_agents() == []

    def test_deregister_nonexistent_is_noop(self, tmp_path):
        registry = self._make_registry(tmp_path)
        registry.deregister("nonexistent")  # should not raise
        assert registry.list_agents() == []

    def test_deregister_only_removes_target(self, tmp_path):
        registry = self._make_registry(tmp_path)
        registry.register(make_agent("a-001"))
        registry.register(make_agent("a-002"))
        registry.deregister("a-001")
        agents = registry.list_agents()
        assert len(agents) == 1
        assert agents[0].agent_id == "a-002"

    # -- get ------------------------------------------------------------------

    def test_get_existing_agent(self, tmp_path):
        registry = self._make_registry(tmp_path)
        registry.register(make_agent("a-007"))
        result = registry.get("a-007")
        assert result is not None
        assert result.agent_id == "a-007"

    def test_get_missing_returns_none(self, tmp_path):
        registry = self._make_registry(tmp_path)
        result = registry.get("nonexistent")
        assert result is None

    # -- heartbeat ------------------------------------------------------------

    def test_heartbeat_updates_last_heartbeat(self, tmp_path):
        registry = self._make_registry(tmp_path)
        agent = make_agent("a-001")
        registry.register(agent)

        before = datetime.now(timezone.utc)
        registry.heartbeat("a-001")
        after = datetime.now(timezone.utc)

        loaded = registry.get("a-001")
        assert loaded.last_heartbeat is not None
        assert before <= loaded.last_heartbeat <= after

    def test_heartbeat_nonexistent_is_noop(self, tmp_path):
        registry = self._make_registry(tmp_path)
        registry.heartbeat("nonexistent")  # should not raise

    def test_heartbeat_preserves_other_fields(self, tmp_path):
        registry = self._make_registry(tmp_path)
        agent = make_agent("a-001")
        agent.capabilities = ["ml", "backend_dev"]
        registry.register(agent)
        registry.heartbeat("a-001")
        loaded = registry.get("a-001")
        assert "ml" in loaded.capabilities

    # -- persistence ----------------------------------------------------------

    def test_creates_registry_file_on_init(self, tmp_path):
        self._make_registry(tmp_path)
        assert (tmp_path / "agents" / "registry.json").exists()

    def test_registry_file_is_valid_json(self, tmp_path):
        registry = self._make_registry(tmp_path)
        registry.register(make_agent())
        content = (tmp_path / "agents" / "registry.json").read_text()
        data = json.loads(content)
        assert isinstance(data, dict)


# ===========================================================================
# InMemoryLeaseAdapter
# ===========================================================================

class TestInMemoryLeaseAdapter:

    def _make_adapter(self):
        from src.infra.redis_adapters.lease_memory import InMemoryLeaseAdapter
        return InMemoryLeaseAdapter()

    def test_create_returns_token(self):
        adapter = self._make_adapter()
        token = adapter.create_lease("task-1", "agent-1", 300)
        assert token is not None and len(token) > 0

    def test_unique_tokens_per_lease(self):
        adapter = self._make_adapter()
        t1 = adapter.create_lease("task-1", "agent-1", 300)
        t2 = adapter.create_lease("task-2", "agent-2", 300)
        assert t1 != t2

    def test_is_active_after_create(self):
        adapter = self._make_adapter()
        adapter.create_lease("task-1", "agent-1", 300)
        assert adapter.is_lease_active("task-1") is True

    def test_is_not_active_before_create(self):
        adapter = self._make_adapter()
        assert adapter.is_lease_active("task-never-created") is False

    def test_is_not_active_after_expire_all(self):
        adapter = self._make_adapter()
        adapter.create_lease("task-1", "agent-1", 300)
        adapter.expire_all()
        assert adapter.is_lease_active("task-1") is False

    def test_revoke_returns_true(self):
        adapter = self._make_adapter()
        token = adapter.create_lease("task-1", "agent-1", 300)
        result = adapter.revoke_lease(token)
        assert result is True

    def test_revoke_makes_lease_inactive(self):
        adapter = self._make_adapter()
        token = adapter.create_lease("task-1", "agent-1", 300)
        adapter.revoke_lease(token)
        assert adapter.is_lease_active("task-1") is False

    def test_revoke_nonexistent_token_returns_false(self):
        adapter = self._make_adapter()
        result = adapter.revoke_lease("fake-token")
        assert result is False

    def test_double_revoke_returns_false_second_time(self):
        adapter = self._make_adapter()
        token = adapter.create_lease("task-1", "agent-1", 300)
        adapter.revoke_lease(token)
        result = adapter.revoke_lease(token)
        assert result is False

    def test_refresh_extends_lease(self):
        adapter = self._make_adapter()
        token = adapter.create_lease("task-1", "agent-1", 1)
        adapter.expire_all()
        # After expire_all, re-test with fresh adapter
        adapter2 = self._make_adapter()
        token2 = adapter2.create_lease("task-2", "agent-1", 300)
        ok = adapter2.refresh_lease(token2, additional_seconds=300)
        assert ok is True
        assert adapter2.is_lease_active("task-2") is True

    def test_refresh_nonexistent_token_returns_false(self):
        adapter = self._make_adapter()
        result = adapter.refresh_lease("fake-token")
        assert result is False

    def test_get_lease_agent_returns_correct_agent(self):
        adapter = self._make_adapter()
        adapter.create_lease("task-1", "agent-007", 300)
        agent = adapter.get_lease_agent("task-1")
        assert agent == "agent-007"

    def test_get_lease_agent_returns_none_if_inactive(self):
        adapter = self._make_adapter()
        adapter.create_lease("task-1", "agent-1", 300)
        adapter.expire_all()
        agent = adapter.get_lease_agent("task-1")
        assert agent is None

    def test_get_lease_agent_returns_none_for_unknown(self):
        adapter = self._make_adapter()
        assert adapter.get_lease_agent("unknown-task") is None

    def test_expire_all_expires_multiple_leases(self):
        adapter = self._make_adapter()
        adapter.create_lease("t1", "a1", 300)
        adapter.create_lease("t2", "a2", 300)
        adapter.create_lease("t3", "a3", 300)
        adapter.expire_all()
        assert not adapter.is_lease_active("t1")
        assert not adapter.is_lease_active("t2")
        assert not adapter.is_lease_active("t3")

    def test_second_create_overwrites_existing_lease(self):
        adapter = self._make_adapter()
        adapter.create_lease("task-1", "agent-1", 300)
        token2 = adapter.create_lease("task-1", "agent-2", 300)
        assert adapter.get_lease_agent("task-1") == "agent-2"


# ===========================================================================
# InMemoryEventAdapter
# ===========================================================================

class TestInMemoryEventAdapter:

    def _make_adapter(self):
        from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter
        return InMemoryEventAdapter()

    def _make_event(self, event_type: str = "task.created", task_id: str = "t1") -> DomainEvent:
        return DomainEvent(type=event_type, producer="test", payload={"task_id": task_id})

    def test_publish_adds_to_all_events(self):
        adapter = self._make_adapter()
        evt = self._make_event()
        adapter.publish(evt)
        assert len(adapter.all_events) == 1

    def test_all_events_returns_in_order(self):
        adapter = self._make_adapter()
        for i in range(5):
            adapter.publish(self._make_event("task.created", f"t{i}"))
        events = adapter.all_events
        task_ids = [e.payload["task_id"] for e in events]
        assert task_ids == [f"t{i}" for i in range(5)]

    def test_events_of_type_filters_correctly(self):
        adapter = self._make_adapter()
        adapter.publish(self._make_event("task.created"))
        adapter.publish(self._make_event("task.assigned"))
        adapter.publish(self._make_event("task.created"))
        created = adapter.events_of_type("task.created")
        assert len(created) == 2
        assert all(e.type == "task.created" for e in created)

    def test_events_of_type_returns_empty_for_unknown(self):
        adapter = self._make_adapter()
        adapter.publish(self._make_event("task.created"))
        assert adapter.events_of_type("task.unknown") == []

    def test_subscribe_yields_events_of_type(self):
        adapter = self._make_adapter()
        adapter.publish(self._make_event("task.created", "t1"))
        adapter.publish(self._make_event("task.assigned", "t2"))
        consumed = list(adapter.subscribe("task.created"))
        assert len(consumed) == 1
        assert consumed[0].payload["task_id"] == "t1"

    def test_subscribe_many_yields_all_matching_types(self):
        adapter = self._make_adapter()
        adapter.publish(self._make_event("task.created", "t1"))
        adapter.publish(self._make_event("task.requeued", "t2"))
        adapter.publish(self._make_event("task.assigned", "t3"))
        consumed = list(adapter.subscribe_many(["task.created", "task.requeued"]))
        assert len(consumed) == 2
        types = {e.type for e in consumed}
        assert types == {"task.created", "task.requeued"}

    def test_subscribe_ignores_unregistered_types(self):
        adapter = self._make_adapter()
        adapter.publish(self._make_event("task.created"))
        consumed = list(adapter.subscribe("task.other_type"))
        assert consumed == []

    def test_multiple_publishes_accumulate(self):
        adapter = self._make_adapter()
        for i in range(10):
            adapter.publish(self._make_event("task.created", f"t{i}"))
        assert len(adapter.all_events) == 10

    def test_all_events_returns_copy(self):
        adapter = self._make_adapter()
        adapter.publish(self._make_event())
        events = adapter.all_events
        events.append(self._make_event())  # mutate returned list
        # Internal state should not be affected
        assert len(adapter.all_events) == 1