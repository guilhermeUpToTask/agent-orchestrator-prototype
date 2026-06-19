"""Integration test for the workflow-file -> SQLite task importer (Phase 3)."""
from __future__ import annotations

import yaml

from src.domain.aggregates.task import TaskAggregate
from src.domain.entities.project import Project
from src.domain.value_objects.task import AgentSelector, ExecutionSpec
from src.infra.db.bootstrap import config_db
from src.infra.db.config_store import SqliteConfigStore
from src.infra.db.importer import import_tasks
from src.infra.db.task_store import SqliteTaskStore


def _write_task(tasks_dir, task_id):
    task = TaskAggregate.create(
        title="t", description="d",
        execution=ExecutionSpec(type="code"),
        agent_selector=AgentSelector(required_capability="code:backend"),
        task_id=task_id,
    )
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / f"{task_id}.yaml").write_text(yaml.dump(task.model_dump(mode="json")))


def test_import_tasks_idempotent_and_stamps_known_project(tmp_path) -> None:
    tasks_dir = tmp_path / "projects" / "My App" / "tasks"
    _write_task(tasks_dir, "task-a")
    _write_task(tasks_dir, "task-b")

    _, sf = config_db(tmp_path)
    config = SqliteConfigStore(sf)
    config.create_project(Project(id="my-app", name="My App", repo_url="r"))
    store = SqliteTaskStore(sf)

    first = import_tasks(orchestrator_home=tmp_path, task_store=store, config_store=config)
    assert set(first) == {"task-a", "task-b"}
    assert store.load("task-a").project_id == "my-app"

    # Re-run: nothing new imported, no duplicates.
    second = import_tasks(orchestrator_home=tmp_path, task_store=store, config_store=config)
    assert second == []
    assert len(store.list_all()) == 2


def test_import_tasks_unknown_project_imported_unscoped(tmp_path) -> None:
    tasks_dir = tmp_path / "projects" / "Orphan" / "tasks"
    _write_task(tasks_dir, "task-x")

    _, sf = config_db(tmp_path)
    store = SqliteTaskStore(sf)
    # No project created -> task imported with project_id=None (no FK crash).
    imported = import_tasks(orchestrator_home=tmp_path, task_store=store)
    assert imported == ["task-x"]
    assert store.load("task-x").project_id is None
