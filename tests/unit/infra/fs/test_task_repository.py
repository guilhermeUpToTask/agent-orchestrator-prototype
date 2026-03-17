from pathlib import Path
import pytest
from src.domain import TaskAggregate, TaskStatus, Assignment, AgentSelector, ExecutionSpec
from src.infra.fs.task_repository import YamlTaskRepository

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

class TestYamlTaskRepository:
    def _make_repo(self, tmp_path: Path):
        return YamlTaskRepository(tmp_path / "tasks")

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
        loaded = repo.load(task.task_id)
        assert loaded.status == TaskStatus.CREATED

    def test_list_all(self, tmp_path):
        repo = self._make_repo(tmp_path)
        for i in range(5):
            repo.save(make_task(f"task-{i:03d}"))
        tasks = repo.list_all()
        assert len(tasks) == 5

    def test_append_history(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task()
        repo.save(task)
        repo.append_history(task.task_id, "custom.event", "tester", {"key": "val"})
        loaded = repo.load(task.task_id)
        assert len(loaded.history) == 1
        assert loaded.history[0].event == "custom.event"
        assert loaded.history[0].detail["key"] == "val"
