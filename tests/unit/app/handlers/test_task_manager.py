import pytest
from unittest.mock import MagicMock
from src.app.handlers.task_manager import TaskManagerHandler, _VersionConflict
from src.domain import TaskAggregate, TaskStatus, AgentProps, AgentSelector, ExecutionSpec, Assignment

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
        self.repo.load.side_effect = lambda tid: task if tid == "t1" else make_task(tid, TaskStatus.FAILED)
        self.repo.update_if_version.return_value = True
        
        self.handler.handle_task_failed("t1")
        
        assert task.status == TaskStatus.REQUEUED
        assert task.retry_policy.attempt == 1
        assert self.events.publish.call_args[0][0].type == "task.requeued"
    def test_handle_task_failed_max_retries(self):
        # We need to capture the task to check status, but return a fresh one on load
        # Actually in this test there is no retry, so return_value is fine if we are careful
        task = make_task("t1", TaskStatus.FAILED)
        task.retry_policy.attempt = 2
        task.retry_policy.max_retries = 2
        self.repo.load.return_value = task
        self.repo.update_if_version.return_value = True
        
        self.handler.handle_task_failed("t1")
        
        assert task.status == TaskStatus.CANCELED
        assert self.events.publish.call_args[0][0].type == "task.canceled"

    def test_handle_task_failed_version_conflict(self):
        self.repo.load.side_effect = lambda tid: make_task(tid, TaskStatus.FAILED)
        # Fail first attempt, succeed second
        self.repo.update_if_version.side_effect = [False, True]
        
        self.handler.handle_task_failed("t1")
        
        assert self.repo.update_if_version.call_count == 2

    def test_handle_task_failed_stale_event(self):
        task = make_task("t1", TaskStatus.SUCCEEDED)
        self.repo.load.return_value = task
        
        self.handler.handle_task_failed("t1")
        
        self.repo.update_if_version.assert_not_called()

    def test_handle_task_failed_task_not_found(self):
        self.repo.load.side_effect = KeyError("t1")
        self.handler.handle_task_failed("t1")
        self.repo.update_if_version.assert_not_called()

    def test_assign_version_conflict(self):
        self.repo.load.side_effect = lambda tid: make_task(tid, TaskStatus.CREATED)
        self.registry.list_agents.return_value = [AgentProps(agent_id="a1", name="A1", capabilities=["c"])]
        self.scheduler.select_agent.return_value = MagicMock(agent_id="a1")
        
        # Conflict on first update_if_version
        self.repo.update_if_version.side_effect = [False, True, True]
        
        self.handler.handle_task_created("t1")
        
        assert self.repo.update_if_version.call_count >= 2

    def test_handle_task_completed_unblocks_dependent(self):
        task1 = make_task("t1", TaskStatus.SUCCEEDED)
        task2 = make_task("t2", TaskStatus.CREATED)
        task2.depends_on = ["t1"]
        
        self.repo.list_all.return_value = [task1, task2]
        self.repo.load.return_value = task2
        self.repo.update_if_version.return_value = True
        self.registry.list_agents.return_value = [AgentProps(agent_id="a1", name="A1", capabilities=["c"])]
        self.scheduler.select_agent.return_value = MagicMock(agent_id="a1")
        
        self.handler.handle_task_completed("t1")
        
        assert task2.status == TaskStatus.ASSIGNED
