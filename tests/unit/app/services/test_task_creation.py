from unittest.mock import MagicMock
from src.app.services.task_creation import TaskCreationService
from src.domain import TaskAggregate, TaskStatus


def _make_svc():
    repo = MagicMock()
    events = MagicMock()
    return TaskCreationService(repo, events), repo, events


class TestTaskCreationService:

    def test_returns_task_aggregate(self):
        svc, _, _ = _make_svc()
        task = svc.create_task(
            title="My Task", description="Doing things",
            capability="coding", files_allowed_to_modify=["main.py"],
        )
        assert isinstance(task, TaskAggregate)

    def test_title_and_description_forwarded(self):
        svc, _, _ = _make_svc()
        task = svc.create_task(
            title="Add login", description="POST /login endpoint",
            capability="backend", files_allowed_to_modify=[],
        )
        assert task.title == "Add login"
        assert task.description == "POST /login endpoint"

    def test_repo_save_called_once(self):
        svc, repo, _ = _make_svc()
        task = svc.create_task(
            title="T", description="D",
            capability="c", files_allowed_to_modify=[],
        )
        repo.save.assert_called_once_with(task)

    def test_event_type_is_task_created(self):
        svc, _, events = _make_svc()
        svc.create_task(title="T", description="D", capability="c", files_allowed_to_modify=[])
        assert events.publish.call_args[0][0].type == "task.created"

    def test_event_payload_contains_task_id(self):
        svc, _, events = _make_svc()
        task = svc.create_task(title="T", description="D", capability="c", files_allowed_to_modify=[])
        payload = events.publish.call_args[0][0].payload
        assert payload["task_id"] == task.task_id

    def test_depends_on_forwarded(self):
        svc, _, _ = _make_svc()
        task = svc.create_task(
            title="T", description="D", capability="c",
            files_allowed_to_modify=[], depends_on=["task-A", "task-B"],
        )
        assert task.depends_on == ["task-A", "task-B"]

    def test_test_command_forwarded(self):
        svc, _, _ = _make_svc()
        task = svc.create_task(
            title="T", description="D", capability="c",
            files_allowed_to_modify=[], test_command="pytest -q",
        )
        assert task.execution.test_command == "pytest -q"

    def test_single_quotes_sanitised_in_test_command(self):
        """Single quotes in test_command are replaced with double quotes for shell safety."""
        svc, _, _ = _make_svc()
        task = svc.create_task(
            title="T", description="D", capability="c",
            files_allowed_to_modify=[], test_command="pytest -k 'auth'",
        )
        assert "'" not in task.execution.test_command
        assert '"auth"' in task.execution.test_command

    def test_max_retries_forwarded(self):
        svc, _, _ = _make_svc()
        task = svc.create_task(
            title="T", description="D", capability="c",
            files_allowed_to_modify=[], max_retries=5,
        )
        assert task.retry_policy.max_retries == 5

    def test_files_allowed_forwarded(self):
        svc, _, _ = _make_svc()
        task = svc.create_task(
            title="T", description="D", capability="c",
            files_allowed_to_modify=["a.py", "b.py"],
        )
        assert task.execution.files_allowed_to_modify == ["a.py", "b.py"]

    def test_initial_status_is_created(self):
        svc, _, _ = _make_svc()
        task = svc.create_task(
            title="T", description="D", capability="c", files_allowed_to_modify=[],
        )
        assert task.status == TaskStatus.CREATED
