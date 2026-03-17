import pytest
from unittest.mock import MagicMock, patch, ANY
from src.app.handlers.worker import WorkerHandler
from src.domain import (
    TaskAggregate, TaskStatus, AgentProps, AgentSelector, ExecutionSpec, Assignment, AgentExecutionResult
)

def make_task(task_id: str, status: TaskStatus) -> TaskAggregate:
    return TaskAggregate(
        task_id=task_id,
        feature_id="f1",
        title="T",
        description="D",
        agent_selector=AgentSelector(required_capability="c"),
        execution=ExecutionSpec(type="t"),
        status=status,
        assignment=Assignment(agent_id="worker-1")
    )

class TestWorkerHandler:
    def setup_method(self):
        self.repo = MagicMock()
        self.registry = MagicMock()
        self.events = MagicMock()
        self.lease = MagicMock()
        self.git = MagicMock()
        self.runtime_factory = MagicMock()
        self.logs = MagicMock()
        self.tests = MagicMock()
        self.handler = WorkerHandler(
            agent_id="worker-1",
            repo_url="git://repo",
            task_repo=self.repo,
            agent_registry=self.registry,
            event_port=self.events,
            lease_port=self.lease,
            git_workspace=self.git,
            runtime_factory=self.runtime_factory,
            logs_port=self.logs,
            test_runner=self.tests
        )

    @patch("src.app.handlers.worker._LeaseRefresher")
    def test_process_success(self, mock_refresher):
        task = make_task("t1", TaskStatus.ASSIGNED)
        self.repo.load.side_effect = lambda tid: task
        self.repo.update_if_version.return_value = True
        
        agent_props = AgentProps(agent_id="worker-1", name="W1")
        self.registry.get.return_value = agent_props
        
        runtime = MagicMock()
        self.runtime_factory.return_value = runtime
        runtime.wait_for_completion.return_value = AgentExecutionResult(success=True, exit_code=0)
        
        self.git.create_workspace.return_value = "/tmp/ws"
        self.git.apply_changes_and_commit.return_value = "sha-abc"
        
        self.handler.process("t1", "p1")
        
        assert task.status == TaskStatus.SUCCEEDED
        assert task.result.commit_sha == "sha-abc"
        self.events.publish.assert_any_call(ANY) # task.started and task.completed
        self.git.cleanup_workspace.assert_called_once_with("/tmp/ws")
        # self.lease.revoke_lease.assert_called_once()
    @patch("src.app.handlers.worker._LeaseRefresher")
    def test_process_agent_failure(self, mock_refresher):
        task = make_task("t1", TaskStatus.ASSIGNED)
        self.repo.load.side_effect = lambda tid: task
        self.repo.update_if_version.return_value = True
        self.registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")
        
        runtime = MagicMock()
        self.runtime_factory.return_value = runtime
        runtime.wait_for_completion.return_value = AgentExecutionResult(success=False, exit_code=1)
        
        self.git.create_workspace.return_value = "/tmp/ws"
        
        self.handler.process("t1", "p1")
        
        assert task.status == TaskStatus.FAILED
        assert "Agent exited with code 1" in task.last_error

    @patch("src.app.handlers.worker._LeaseRefresher")
    def test_process_forbidden_edit(self, mock_refresher):
        from src.domain import ForbiddenFileEditError
        task = make_task("t1", TaskStatus.ASSIGNED)
        self.repo.load.side_effect = lambda tid: task
        self.repo.update_if_version.return_value = True
        self.registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")
        
        runtime = MagicMock()
        self.runtime_factory.return_value = runtime
        runtime.wait_for_completion.return_value = AgentExecutionResult(success=True, exit_code=0)
        
        self.git.create_workspace.return_value = "/tmp/ws"
        self.git.get_modified_files.return_value = ["forbidden.py"]
        
        # Mock validation failure
        with patch.object(ExecutionSpec, "validate_modifications", side_effect=ForbiddenFileEditError(["forbidden.py"])):
            self.handler.process("t1", "p1")
        
        assert task.status == TaskStatus.FAILED
        assert "Forbidden file edits" in task.last_error

    @patch("src.app.handlers.worker._LeaseRefresher")
    def test_process_version_conflict_recovery(self, mock_refresher):
        # We need to capture the task objects returned by load
        tasks = []
        def load_side_effect(tid):
            t = make_task(tid, TaskStatus.ASSIGNED)
            tasks.append(t)
            return t
            
        self.repo.load.side_effect = load_side_effect
        # load -> version_v1
        # update_if_version fails once
        self.repo.update_if_version.side_effect = [False, True, True] # start_task, persist_success
        
        self.registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")
        runtime = MagicMock()
        self.runtime_factory.return_value = runtime
        runtime.wait_for_completion.return_value = AgentExecutionResult(success=True, exit_code=0)
        self.git.create_workspace.return_value = "/tmp/ws"
        self.git.apply_changes_and_commit.return_value = "sha-abc"
        
        self.handler.process("t1", "p1")
        
        assert self.repo.update_if_version.call_count >= 2
        # The last task loaded should be the one that succeeded
        assert tasks[-1].status == TaskStatus.SUCCEEDED
