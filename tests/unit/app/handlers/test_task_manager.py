import pytest
from unittest.mock import MagicMock
from src.app.handlers.task_manager import TaskManagerHandler, _VersionConflict
from src.core.models import TaskAggregate, TaskStatus, AgentProps, AgentSelector, ExecutionSpec, Assignment

def make_task(task_id: str, status: TaskStatus) -> TaskAggregate:
    return TaskAggregate(
        task_id=task_id,
        feature_id="f1",
        title="T",
        description="D",
        agent_selector=AgentSelector(required_capability="c"),
        execution=ExecutionSpec(type="t"),
        status=status,
    )

class TestTaskManagerHandler:
    def setup_method(self):
        self.repo = MagicMock()
        self.registry = MagicMock()
        self.events = MagicMock()
        self.lease = MagicMock()
        self.scheduler = MagicMock()
        self.handler = TaskManagerHandler(self.repo, self.registry, self.events, self.lease, scheduler=self.scheduler)

    def test_handle_task_created_success(self):
        task = make_task("t1", TaskStatus.CREATED)
        self.repo.load.return_value = task
        self.repo.update_if_version.return_value = True
        agent = AgentProps(agent_id="a1", name="A1", capabilities=["c"])
        self.registry.list_agents.return_value = [agent]
        self.scheduler.select_agent.return_value = agent
        self.lease.create_lease.return_value = "token-123"
        
        result = self.handler.handle_task_created("t1")
        
        assert result is True
        assert task.status == TaskStatus.ASSIGNED
        assert task.assignment.agent_id == "a1"
        assert task.assignment.lease_token == "token-123"
        self.events.publish.assert_called_once()
        assert self.events.publish.call_args[0][0].type == "task.assigned"

    def test_handle_task_failed_requeue(self):
        task = make_task("t1", TaskStatus.FAILED)
        task.retry_policy.attempt = 0
        task.retry_policy.max_retries = 2
        self.repo.load.return_value = task
        self.repo.update_if_version.return_value = True
        
        self.handler.handle_task_failed("t1")
        
        assert task.status == TaskStatus.REQUEUED
        assert task.retry_policy.attempt == 1
        assert self.events.publish.call_args[0][0].type == "task.requeued"
