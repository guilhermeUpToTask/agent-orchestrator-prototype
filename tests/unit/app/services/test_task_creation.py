import pytest
from unittest.mock import MagicMock
from src.app.services.task_creation import TaskCreationService
from src.domain import TaskAggregate

class TestTaskCreationService:
    def test_create_task(self):
        repo = MagicMock()
        events = MagicMock()
        svc = TaskCreationService(repo, events)
        
        task = svc.create_task(
            title="My Task",
            description="Doing things",
            capability="coding",
            files_allowed_to_modify=["main.py"]
        )
        
        assert isinstance(task, TaskAggregate)
        assert task.title == "My Task"
        repo.save.assert_called_once_with(task)
        events.publish.assert_called_once()
        assert events.publish.call_args[0][0].type == "task.created"
