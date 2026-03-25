"""
tests/unit/app/services/test_worker_execution.py — TaskExecuteUseCase tests.
"""
from __future__ import annotations

from unittest.mock import MagicMock, ANY

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


class TestWorkerExecutionService:

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_execute_success(self):
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

    def test_execute_agent_failure(self):
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

    def test_execute_forbidden_edit(self):
        from unittest.mock import patch
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

    def test_start_cas_retry(self):
        initial = _make_task()
        retry_assigned = _make_task()
        after_start = _make_task()
        after_start.start()

        svc = _make_service()
        svc._task_repo.load.side_effect = [
            initial, # 1. _start_task_with_retry (fails CAS)
            initial, # 2. _start_task_with_retry (retry, succeeds CAS)
            retry_assigned, # 3. _persist_success (succeeds CAS)
            after_start,
        ]
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
        completed = [
            c for c in svc._events.publish.call_args_list
            if c[0][0].type == "task.completed"
        ]
        assert len(completed) == 1

    # ------------------------------------------------------------------
    # Wrong agent → raises before try/except block
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

    # ------------------------------------------------------------------
    # _start_task_with_retry — status changed under us during CAS
    # ------------------------------------------------------------------

    def test_start_raises_when_task_status_changes_during_cas(self):
        assigned_initial = _make_task()
        succeeded_reload = _make_task()
        succeeded_reload.status = TaskStatus.SUCCEEDED

        svc = _make_service()
        call_count = {"n": 0}

        def load_side(tid):
            call_count["n"] += 1
            return assigned_initial if call_count["n"] == 1 else succeeded_reload

        svc._task_repo.load.side_effect = load_side
        svc._task_repo.update_if_version.return_value = True
        svc._registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")
        svc._git.create_workspace.return_value = "/tmp/ws"

        svc.execute("t-001", "p-001", "worker-1")

        started_events = [
            c for c in svc._events.publish.call_args_list
            if c[0][0].type == "task.started"
        ]
        assert started_events == []

    def test_start_raises_after_max_cas_retries_exhausted(self):
        svc = _make_service()
        svc._task_repo.load.side_effect = lambda tid: _make_task(task_id=tid)
        svc._task_repo.update_if_version.return_value = False
        svc._registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")
        svc._git.create_workspace.return_value = "/tmp/ws"

        svc.execute("t-001", "p-001", "worker-1")

        failed_events = [
            c for c in svc._events.publish.call_args_list
            if c[0][0].type == "task.failed"
        ]
        assert failed_events == []

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

        svc._task_repo.update_if_version.assert_not_called()
        svc._events.publish.assert_not_called()

    # ------------------------------------------------------------------
    # _build_env — non-string runtime_config values are JSON-encoded
    # ------------------------------------------------------------------

    def test_non_string_runtime_config_values_are_json_encoded(self):
        task = _make_task()
        svc = _make_service()
        svc._task_repo.load.return_value = task
        svc._task_repo.update_if_version.return_value = True
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
        svc._task_repo.update_if_version.return_value = True
        svc._registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")

        runtime = MagicMock()
        runtime.wait_for_completion.return_value = AgentExecutionResult(
            success=True, exit_code=0
        )
        runtime.terminate_session.side_effect = RuntimeError("session already gone")
        svc._runtime_factory.return_value = runtime
        svc._git.create_workspace.return_value = "/tmp/ws"
        svc._git.apply_changes_and_commit.return_value = "sha-abc"

        svc.execute("t-001", "p-001", "worker-1")

        assert task.status == TaskStatus.SUCCEEDED

    # ------------------------------------------------------------------
    # _start_task_with_retry — assignment stolen mid-CAS
    # ------------------------------------------------------------------

    def test_start_raises_when_assignment_changes_during_cas(self):
        initial = _make_task()

        def make_reassigned():
            t = _make_task()
            t.assignment = Assignment(agent_id="other-worker")
            return t

        svc = _make_service()
        call_count = {"n": 0}

        def load_side(tid):
            call_count["n"] += 1
            return initial if call_count["n"] == 1 else make_reassigned()

        svc._task_repo.load.side_effect = load_side
        svc._task_repo.update_if_version.return_value = True
        svc._registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")
        svc._git.create_workspace.return_value = "/tmp/ws"

        svc.execute("t-001", "p-001", "worker-1")

        failed = [c for c in svc._events.publish.call_args_list
                  if c[0][0].type == "task.failed"]
        assert len(failed) == 1
        assert "assignment changed" in failed[0][0][0].payload["reason"]

    # ------------------------------------------------------------------
    # _handle_failure — when the failure handler itself errors
    # ------------------------------------------------------------------

    def test_handle_failure_swallows_its_own_exception(self):
        svc = _make_service()
        task = _make_task()
        task.start()

        svc._task_repo.load.return_value = task
        svc._task_repo.update_if_version.side_effect = RuntimeError("disk full")

        svc._handle_failure(task, "worker-1", "some reason")

        svc._events.publish.assert_not_called()

    def test_success_does_not_publish_completed_when_persist_never_succeeds(self):
        task = _make_task()
        in_progress_states = []
        for _ in range(6):
            fresh = _make_task(status=TaskStatus.ASSIGNED)
            fresh.start()
            in_progress_states.append(fresh)

        svc = _make_service()
        svc._task_repo.load.side_effect = [task, task, task] + in_progress_states
        svc._task_repo.update_if_version.side_effect = [True] + [False] * 5
        svc._registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")

        runtime = MagicMock()
        svc._runtime_factory.return_value = runtime
        runtime.wait_for_completion.return_value = AgentExecutionResult(
            success=True, exit_code=0
        )
        svc._git.create_workspace.return_value = "/tmp/ws"
        svc._git.apply_changes_and_commit.return_value = "sha-abc"

        svc.execute("t-001", "p-001", "worker-1")

        completed = [
            c for c in svc._events.publish.call_args_list
            if c[0][0].type == "task.completed"
        ]
        failed = [
            c for c in svc._events.publish.call_args_list
            if c[0][0].type == "task.failed"
        ]
        assert completed == []
        assert failed == []
