"""Port-parity + crash-recovery tests for the task stores (Phase 3).

The same TaskRepositoryPort lifecycle must pass on both the YAML adapter and the
SQLite adapter, proving the cutover is behaviour-preserving.
"""
from __future__ import annotations

import pytest

from src.domain.aggregates.task import TaskAggregate
from src.domain.value_objects.status import TaskStatus
from src.domain.value_objects.task import AgentSelector, ExecutionSpec
from src.infra.db.bootstrap import config_db
from src.infra.db.task_store import SqliteTaskStore
from src.infra.fs.task_repository import YamlTaskRepository


def _make_task(task_id: str = "task-1") -> TaskAggregate:
    return TaskAggregate.create(
        title="t",
        description="d",
        execution=ExecutionSpec(type="code"),
        agent_selector=AgentSelector(required_capability="code:backend"),
        task_id=task_id,
    )


@pytest.fixture(params=["yaml", "sqlite"])
def repo(request, tmp_path):
    if request.param == "yaml":
        return YamlTaskRepository(tmp_path / "tasks")
    _, sf = config_db(tmp_path)
    return SqliteTaskStore(sf)


class TestParity:
    def test_save_and_load(self, repo) -> None:
        task = _make_task()
        repo.save(task)
        loaded = repo.load("task-1")
        assert loaded.task_id == "task-1"
        assert loaded.status == TaskStatus.CREATED
        assert loaded.state_version == task.state_version

    def test_load_missing_raises_keyerror(self, repo) -> None:
        with pytest.raises(KeyError):
            repo.load("ghost")

    def test_get_missing_returns_none(self, repo) -> None:
        assert repo.get("ghost") is None

    def test_update_if_version_success(self, repo) -> None:
        task = _make_task()
        repo.save(task)
        expected = task.state_version
        task.cancel("done")  # CREATED -> CANCELED, bumps state_version
        assert repo.update_if_version("task-1", task, expected) is True
        reloaded = repo.load("task-1")
        assert reloaded.status == TaskStatus.CANCELED
        assert reloaded.state_version == expected + 1

    def test_update_if_version_stale_returns_false(self, repo) -> None:
        task = _make_task()
        repo.save(task)
        task.cancel("done")
        assert repo.update_if_version("task-1", task, 999) is False
        assert repo.load("task-1").status == TaskStatus.CREATED

    def test_update_if_version_missing_raises(self, repo) -> None:
        with pytest.raises(KeyError):
            repo.update_if_version("ghost", _make_task("ghost"), 1)

    def test_list_all(self, repo) -> None:
        repo.save(_make_task("a"))
        repo.save(_make_task("b"))
        ids = {t.task_id for t in repo.list_all()}
        assert ids == {"a", "b"}

    def test_append_history_no_version_bump(self, repo) -> None:
        task = _make_task()
        repo.save(task)
        before = repo.load("task-1").state_version
        repo.append_history("task-1", "note", "operator", {"k": "v"})
        after = repo.load("task-1")
        assert after.state_version == before
        assert any(h.event == "note" for h in after.history)

    def test_delete(self, repo) -> None:
        repo.save(_make_task())
        assert repo.delete("task-1") is True
        assert repo.delete("task-1") is False
        assert repo.get("task-1") is None


class TestDurability:
    """commit-then-emit: committed state is durable before any event is emitted."""

    def test_committed_state_visible_to_fresh_store(self, tmp_path) -> None:
        engine, sf = config_db(tmp_path)
        store = SqliteTaskStore(sf)
        task = _make_task()
        store.save(task)
        expected = task.state_version
        task.cancel("crash-before-emit")
        # update_if_version commits; simulate a crash *before* the XADD by simply
        # not publishing. A brand-new store/session must still see the change.
        assert store.update_if_version("task-1", task, expected) is True

        _, sf2 = config_db(tmp_path)
        recovered = SqliteTaskStore(sf2).load("task-1")
        assert recovered.status == TaskStatus.CANCELED

    def test_transitions_audited(self, tmp_path) -> None:
        from sqlalchemy import text

        engine, sf = config_db(tmp_path)
        store = SqliteTaskStore(sf)
        task = _make_task()
        store.save(task)
        expected = task.state_version
        task.cancel("done")
        store.update_if_version("task-1", task, expected)
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT event FROM task_transitions WHERE task_id='task-1'")
            ).all()
        events = {r[0] for r in rows}
        assert "task.canceled" in events
