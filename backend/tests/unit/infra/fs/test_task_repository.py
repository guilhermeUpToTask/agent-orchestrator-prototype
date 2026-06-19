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

    def test_delete_removes_yaml_file(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task("task-del")
        repo.save(task)
        result = repo.delete("task-del")
        assert result is True
        assert not (tmp_path / "tasks" / "task-del.yaml").exists()

    def test_delete_returns_false_when_not_found(self, tmp_path):
        repo = self._make_repo(tmp_path)
        assert repo.delete("nonexistent-task") is False

    def test_load_raises_after_delete(self, tmp_path):
        repo = self._make_repo(tmp_path)
        repo.save(make_task())
        repo.delete("task-001")
        with pytest.raises(KeyError):
            repo.load("task-001")

    def test_update_if_version_raises_key_error_for_nonexistent(self, tmp_path):
        repo = self._make_repo(tmp_path)
        task = make_task()
        with pytest.raises(KeyError):
            repo.update_if_version("nonexistent-task", task, expected_version=1)

    def test_append_history_raises_key_error_for_nonexistent(self, tmp_path):
        repo = self._make_repo(tmp_path)
        with pytest.raises(KeyError):
            repo.append_history("nonexistent-task", "some.event", "actor", {})

    def test_list_all_quarantines_corrupt_file(self, tmp_path):
        repo = self._make_repo(tmp_path)
        (tmp_path / "tasks" / "task-corrupt.yaml").write_text(": bad: yaml: {{{{")
        tasks = repo.list_all()
        assert tasks == []
        assert (tmp_path / "tasks" / "quarantine" / "task-corrupt.yaml").exists()

    def test_list_all_skips_corrupt_continues_valid(self, tmp_path):
        repo = self._make_repo(tmp_path)
        repo.save(make_task("task-good"))
        (tmp_path / "tasks" / "task-corrupt.yaml").write_text(": {{{{")
        tasks = repo.list_all()
        assert len(tasks) == 1
        assert tasks[0].task_id == "task-good"

    def test_get_returns_task_when_found(self, tmp_path):
        """TaskRepositoryPort.get() returns the task when it exists."""
        repo = self._make_repo(tmp_path)
        repo.save(make_task("task-get"))
        result = repo.get("task-get")
        assert result is not None
        assert result.task_id == "task-get"

    def test_get_returns_none_when_not_found(self, tmp_path):
        """TaskRepositoryPort.get() returns None instead of raising KeyError."""
        repo = self._make_repo(tmp_path)
        assert repo.get("nonexistent") is None

    def test_list_all_skips_empty_yaml_files(self, tmp_path):
        """An empty YAML file (race with atomic write) is skipped silently."""
        repo = self._make_repo(tmp_path)
        repo.save(make_task("task-good"))
        (tmp_path / "tasks" / "task-empty.yaml").write_text("")  # None when parsed
        tasks = repo.list_all()
        assert len(tasks) == 1
        assert tasks[0].task_id == "task-good"

    def test_list_all_continues_when_quarantine_move_fails(self, tmp_path, monkeypatch):
        """If shutil.move() fails during quarantine, list_all() still returns valid tasks."""
        import shutil
        repo = self._make_repo(tmp_path)
        repo.save(make_task("task-good"))
        (tmp_path / "tasks" / "task-corrupt.yaml").write_text(": {{{{")

        original_move = shutil.move

        def failing_move(src, dst):
            if "corrupt" in str(src):
                raise OSError("permission denied")
            return original_move(src, dst)

        monkeypatch.setattr(shutil, "move", failing_move)

        tasks = repo.list_all()
        # Valid task must still be returned despite quarantine failure
        assert len(tasks) == 1
        assert tasks[0].task_id == "task-good"

    def test_abstract_delete_default_returns_false(self, tmp_path):
        """TaskRepositoryPort.delete() base implementation returns False (not found)."""
        from src.domain.repositories.task_repository import TaskRepositoryPort
        # Use the concrete YamlTaskRepository — it inherits delete() from the base
        # and overrides it, but we can call the base directly to verify the default
        repo = self._make_repo(tmp_path)
        result = TaskRepositoryPort.delete(repo, "nonexistent")
        assert result is False
