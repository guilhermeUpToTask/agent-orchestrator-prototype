"""
tests/unit/app/services/test_worker_execution.py — TaskExecuteUseCase tests.

Contract under test: the worker NEVER writes task state. It publishes
task.execution_started / task.execution_succeeded / task.execution_failed
and the task manager (TaskRecordResultUseCase, tested separately) applies
the transitions.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.app.usecases.task_execute import TaskExecuteUseCase as WorkerExecutionService
from src.domain import (
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
    # Default lease_refresher_factory returns a mock handle with start/stop.
    mock_refresher = MagicMock()
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
        lease_refresher_factory=MagicMock(return_value=mock_refresher),
    )
    defaults.update(overrides)
    return WorkerExecutionService(**defaults)


def _published_types(svc) -> list[str]:
    return [c[0][0].type for c in svc._events.publish.call_args_list]


def _published_of_type(svc, event_type: str):
    return [
        c[0][0]
        for c in svc._events.publish.call_args_list
        if c[0][0].type == event_type
    ]


class TestWorkerExecutionService:

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_execute_success_publishes_results_and_never_writes(self):
        task = _make_task()
        svc = _make_service()

        svc._task_repo.load.return_value = task
        svc._registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")

        runtime = MagicMock()
        svc._runtime_factory.return_value = runtime
        runtime.wait_for_completion.return_value = AgentExecutionResult(
            success=True, exit_code=0
        )
        svc._git.create_workspace.return_value = "/tmp/ws"
        svc._git.apply_changes_and_commit.return_value = "sha-abc"
        svc._git.get_modified_files.return_value = []

        svc.execute("t-001", "p-001", "worker-1")

        # Single-writer contract: zero task-state writes from the worker.
        svc._task_repo.update_if_version.assert_not_called()
        svc._task_repo.save.assert_not_called()

        assert _published_types(svc) == [
            "task.execution_started",
            "task.execution_succeeded",
        ]
        succeeded = _published_of_type(svc, "task.execution_succeeded")[0]
        assert succeeded.payload["task_id"] == "t-001"
        assert succeeded.payload["agent_id"] == "worker-1"
        assert succeeded.payload["commit_sha"] == "sha-abc"

        svc._git.cleanup_workspace.assert_called_once_with("/tmp/ws")

    # ------------------------------------------------------------------
    # Agent failure → task.execution_failed
    # ------------------------------------------------------------------

    def test_execute_agent_failure(self):
        task = _make_task()
        svc = _make_service()

        svc._task_repo.load.return_value = task
        svc._registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")

        runtime = MagicMock()
        svc._runtime_factory.return_value = runtime
        runtime.wait_for_completion.return_value = AgentExecutionResult(
            success=False, exit_code=1
        )
        svc._git.create_workspace.return_value = "/tmp/ws"
        svc._git.get_modified_files.return_value = []

        svc.execute("t-001", "p-001", "worker-1")

        svc._task_repo.update_if_version.assert_not_called()
        failed = _published_of_type(svc, "task.execution_failed")
        assert len(failed) == 1
        assert "Agent exited with code 1" in failed[0].payload["reason"]

    # ------------------------------------------------------------------
    # Forbidden file edit → task.execution_failed
    # ------------------------------------------------------------------

    def test_execute_forbidden_edit(self):
        from unittest.mock import patch
        task = _make_task()
        task.execution = ExecutionSpec(type="code", files_allowed_to_modify=["ok.py"])
        svc = _make_service()

        svc._task_repo.load.return_value = task
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

        svc._task_repo.update_if_version.assert_not_called()
        failed = _published_of_type(svc, "task.execution_failed")
        assert len(failed) == 1
        assert "Forbidden file edits" in failed[0].payload["reason"]

    # ------------------------------------------------------------------
    # Wrong agent → raises before any event
    # ------------------------------------------------------------------

    def test_wrong_agent_raises(self):
        task = _make_task(agent_id="other-agent")
        svc = _make_service()
        svc._task_repo.load.return_value = task

        with pytest.raises(RuntimeError, match="not this worker"):
            svc.execute("t-001", "p-001", "worker-1")

        assert task.status == TaskStatus.ASSIGNED
        svc._events.publish.assert_not_called()

    # ------------------------------------------------------------------
    # Workspace is always cleaned up even on error
    # ------------------------------------------------------------------

    def test_workspace_cleanup_on_exception(self):
        task = _make_task()
        svc = _make_service()
        svc._task_repo.load.return_value = task
        svc._registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")

        svc._git.create_workspace.return_value = "/tmp/ws"
        svc._git.get_modified_files.return_value = []
        svc._runtime_factory.return_value = MagicMock(
            wait_for_completion=MagicMock(
                return_value=AgentExecutionResult(success=False, exit_code=99)
            )
        )

        svc.execute("t-001", "p-001", "worker-1")

        svc._git.cleanup_workspace.assert_called_once_with("/tmp/ws")

    # ------------------------------------------------------------------
    # _handle_failure — skips when task already left active state
    # ------------------------------------------------------------------

    def test_handle_failure_skips_when_task_not_active(self):
        from src.domain.value_objects.task import TaskResult
        svc = _make_service()

        task = _make_task()
        task.start()
        task.complete(TaskResult(commit_sha="abc"))

        svc._handle_failure(task, "worker-1", "some transient error")

        svc._events.publish.assert_not_called()

    # ------------------------------------------------------------------
    # _handle_failure — when the failure publish itself errors
    # ------------------------------------------------------------------

    def test_handle_failure_swallows_its_own_exception(self):
        svc = _make_service()
        task = _make_task()
        task.start()

        svc._events.publish.side_effect = RuntimeError("redis down")

        # Must not propagate — the worker's finally-block cleanup depends on it.
        svc._handle_failure(task, "worker-1", "some reason")

    # ------------------------------------------------------------------
    # _build_env — non-string runtime_config values are JSON-encoded
    # ------------------------------------------------------------------

    def test_non_string_runtime_config_values_are_json_encoded(self):
        task = _make_task()
        svc = _make_service()
        svc._task_repo.load.return_value = task
        agent = AgentProps(
            agent_id="worker-1", name="W1",
            runtime_config={
                "STRING_KEY": "plain-string",
                "INT_KEY": 42,
                "BOOL_KEY": True,
                "DICT_KEY": {"model": "claude-3"},
            },
        )
        svc._registry.get.return_value = agent

        captured_env: dict = {}

        def capture_session(agent_props, ws_path, env):
            captured_env.update(env)
            return MagicMock()

        runtime = MagicMock()
        runtime.start_session.side_effect = capture_session
        runtime.wait_for_completion.return_value = AgentExecutionResult(
            success=True, exit_code=0
        )
        svc._runtime_factory.return_value = runtime
        svc._git.create_workspace.return_value = "/tmp/ws"
        svc._git.apply_changes_and_commit.return_value = "sha-abc"
        svc._git.get_modified_files.return_value = []

        svc.execute("t-001", "p-001", "worker-1")

        assert captured_env["STRING_KEY"] == "plain-string"
        assert captured_env["INT_KEY"] == "42"
        assert captured_env["BOOL_KEY"] == "true"
        import json
        assert json.loads(captured_env["DICT_KEY"]) == {"model": "claude-3"}

    # ------------------------------------------------------------------
    # terminate_session exception is swallowed in finally block
    # ------------------------------------------------------------------

    def test_terminate_session_exception_is_swallowed(self):
        task = _make_task()
        svc = _make_service()
        svc._task_repo.load.return_value = task
        svc._registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")

        runtime = MagicMock()
        runtime.wait_for_completion.return_value = AgentExecutionResult(
            success=True, exit_code=0
        )
        runtime.terminate_session.side_effect = RuntimeError("session already gone")
        svc._runtime_factory.return_value = runtime
        svc._git.create_workspace.return_value = "/tmp/ws"
        svc._git.apply_changes_and_commit.return_value = "sha-abc"
        svc._git.get_modified_files.return_value = []

        svc.execute("t-001", "p-001", "worker-1")

        assert len(_published_of_type(svc, "task.execution_succeeded")) == 1
