"""
tests/unit/app/services/test_worker_execution.py — WorkerExecutionService tests.

These tests cover the execution pipeline logic that was extracted from
WorkerHandler into WorkerExecutionService during Phase 2 refactoring.
WorkerHandler is now a thin router; the pipeline logic lives here.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, ANY

import pytest

from src.app.services.worker_execution import WorkerExecutionService
from src.core.models import (
    AgentExecutionResult,
    AgentProps,
    AgentSelector,
    Assignment,
    ExecutionSpec,
    ForbiddenFileEditError,
    TaskAggregate,
    TaskStatus,
)


def _make_task(
    task_id: str = "t-001",
    status: TaskStatus = TaskStatus.ASSIGNED,
    agent_id: str = "worker-1",
) -> TaskAggregate:
    return TaskAggregate(
        task_id=task_id,
        feature_id="feat-001",
        title="T",
        description="D",
        agent_selector=AgentSelector(required_capability="code"),
        execution=ExecutionSpec(type="code"),
        status=status,
        assignment=Assignment(agent_id=agent_id),
    )


def _make_service(**overrides) -> WorkerExecutionService:
    defaults = dict(
        repo_url="git://repo",
        task_repo=MagicMock(),
        agent_registry=MagicMock(),
        event_port=MagicMock(),
        lease_port=MagicMock(),
        git_workspace=MagicMock(),
        runtime_factory=MagicMock(),
        logs_port=MagicMock(),
        test_runner=MagicMock(),
    )
    defaults.update(overrides)
    return WorkerExecutionService(**defaults)


class TestWorkerExecutionService:

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    @patch("src.app.services.worker_execution.LeaseRefresher")
    def test_execute_success(self, mock_refresher_cls):
        task = _make_task()
        svc = _make_service()

        svc._task_repo.load.return_value = task
        svc._task_repo.update_if_version.return_value = True
        svc._registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")

        runtime = MagicMock()
        svc._runtime_factory.return_value = runtime
        runtime.wait_for_completion.return_value = AgentExecutionResult(
            success=True, exit_code=0
        )
        svc._git.create_workspace.return_value = "/tmp/ws"
        svc._git.apply_changes_and_commit.return_value = "sha-abc"

        svc.execute("t-001", "p-001", "worker-1")

        assert task.status == TaskStatus.SUCCEEDED
        assert task.result.commit_sha == "sha-abc"
        svc._events.publish.assert_any_call(ANY)
        svc._git.cleanup_workspace.assert_called_once_with("/tmp/ws")

    # ------------------------------------------------------------------
    # Agent failure → task.failed
    # ------------------------------------------------------------------

    @patch("src.app.services.worker_execution.LeaseRefresher")
    def test_execute_agent_failure(self, mock_refresher_cls):
        task = _make_task()
        svc = _make_service()

        svc._task_repo.load.return_value = task
        svc._task_repo.update_if_version.return_value = True
        svc._registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")

        runtime = MagicMock()
        svc._runtime_factory.return_value = runtime
        runtime.wait_for_completion.return_value = AgentExecutionResult(
            success=False, exit_code=1
        )
        svc._git.create_workspace.return_value = "/tmp/ws"

        svc.execute("t-001", "p-001", "worker-1")

        assert task.status == TaskStatus.FAILED
        assert "Agent exited with code 1" in task.last_error

    # ------------------------------------------------------------------
    # Forbidden file edit → task.failed
    # ------------------------------------------------------------------

    @patch("src.app.services.worker_execution.LeaseRefresher")
    def test_execute_forbidden_edit(self, mock_refresher_cls):
        task = _make_task()
        task.execution = ExecutionSpec(type="code", files_allowed_to_modify=["ok.py"])
        svc = _make_service()

        svc._task_repo.load.return_value = task
        svc._task_repo.update_if_version.return_value = True
        svc._registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")

        runtime = MagicMock()
        svc._runtime_factory.return_value = runtime
        runtime.wait_for_completion.return_value = AgentExecutionResult(
            success=True, exit_code=0
        )
        svc._git.create_workspace.return_value = "/tmp/ws"
        svc._git.get_modified_files.return_value = ["forbidden.py"]

        with patch.object(
            ExecutionSpec, "validate_modifications",
            side_effect=ForbiddenFileEditError(["forbidden.py"]),
        ):
            svc.execute("t-001", "p-001", "worker-1")

        assert task.status == TaskStatus.FAILED
        assert "Forbidden file edits" in task.last_error

    # ------------------------------------------------------------------
    # CAS retry on start()
    # ------------------------------------------------------------------

    @patch("src.app.services.worker_execution.LeaseRefresher")
    def test_start_cas_retry(self, mock_refresher_cls):
        tasks_returned = []

        def _load(tid):
            t = _make_task(task_id=tid)
            tasks_returned.append(t)
            return t

        svc = _make_service()
        svc._task_repo.load.side_effect = _load
        # First update_if_version (start CAS) fails once, then succeeds
        svc._task_repo.update_if_version.side_effect = [False, True, True]
        svc._registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")

        runtime = MagicMock()
        svc._runtime_factory.return_value = runtime
        runtime.wait_for_completion.return_value = AgentExecutionResult(
            success=True, exit_code=0
        )
        svc._git.create_workspace.return_value = "/tmp/ws"
        svc._git.apply_changes_and_commit.return_value = "sha-abc"

        svc.execute("t-001", "p-001", "worker-1")

        assert svc._task_repo.update_if_version.call_count >= 2
        assert tasks_returned[-1].status == TaskStatus.SUCCEEDED

    # ------------------------------------------------------------------
    # Wrong agent → assignment validation raises before try/except block
    # This is intentional: wrong-agent is a programming error, not a task
    # failure. The task should NOT be transitioned to FAILED so the correct
    # agent can still process it. The error propagates to the event loop.
    # ------------------------------------------------------------------

    @patch("src.app.services.worker_execution.LeaseRefresher")
    def test_wrong_agent_raises(self, mock_refresher_cls):
        task = _make_task(agent_id="other-agent")
        svc = _make_service()
        svc._task_repo.load.return_value = task

        with pytest.raises(RuntimeError, match="not this worker"):
            svc.execute("t-001", "p-001", "worker-1")

        # Task must remain ASSIGNED so the correct agent can still pick it up
        assert task.status == TaskStatus.ASSIGNED
        # No failure event should be emitted
        svc._events.publish.assert_not_called()

    # ------------------------------------------------------------------
    # Workspace is always cleaned up even on error
    # ------------------------------------------------------------------

    @patch("src.app.services.worker_execution.LeaseRefresher")
    def test_workspace_cleanup_on_exception(self, mock_refresher_cls):
        task = _make_task()
        svc = _make_service()
        svc._task_repo.load.return_value = task
        svc._task_repo.update_if_version.return_value = True
        svc._registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")

        svc._git.create_workspace.return_value = "/tmp/ws"
        svc._runtime_factory.return_value = MagicMock(
            wait_for_completion=MagicMock(
                return_value=AgentExecutionResult(success=False, exit_code=99)
            )
        )

        svc.execute("t-001", "p-001", "worker-1")

        svc._git.cleanup_workspace.assert_called_once_with("/tmp/ws")
