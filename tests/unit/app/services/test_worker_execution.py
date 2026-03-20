"""
tests/unit/app/services/test_worker_execution.py — TaskExecuteUseCase tests.

The execution pipeline was moved from WorkerExecutionService (Phase 2)
into TaskExecuteUseCase (Phase 7). This file imports from both paths:
the new canonical location and the backward-compat re-export in services/.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, ANY

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

    @patch("src.app.usecases.task_execute.LeaseRefresher")
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

    @patch("src.app.usecases.task_execute.LeaseRefresher")
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

    @patch("src.app.usecases.task_execute.LeaseRefresher")
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

    @patch("src.app.usecases.task_execute.LeaseRefresher")
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

    @patch("src.app.usecases.task_execute.LeaseRefresher")
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

    @patch("src.app.usecases.task_execute.LeaseRefresher")
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

    # ------------------------------------------------------------------
    # _start_task_with_retry — status changed under us during CAS
    # ------------------------------------------------------------------

    @patch("src.app.usecases.task_execute.LeaseRefresher")
    def test_start_raises_when_task_status_changes_during_cas(self, mock_refresher_cls):
        """
        execute() loads the task once for _validate_assignment (call 1: ASSIGNED, passes).
        _start_task_with_retry() reloads inside its CAS loop (call 2+).
        If the reloaded task is no longer ASSIGNED (worker raced us to SUCCEEDED),
        a RuntimeError is raised — the execute() except block logs it and calls
        _handle_failure with the original ASSIGNED task object.
        """
        assigned_initial = _make_task()   # first load: passes _validate_assignment
        succeeded_reload = _make_task()   # second load inside CAS loop: status changed
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

        # RuntimeError from _start_task_with_retry is caught by the generic
        # except handler in execute(), so execute() itself does NOT re-raise —
        # it calls _handle_failure and then returns normally.
        svc.execute("t-001", "p-001", "worker-1")

        # The CAS write inside _start_task_with_retry must not have succeeded
        # (no successful IN_PROGRESS write), so update_if_version is not called.
        # The failure handler may call it; but no task.started event must exist.
        started_events = [
            c for c in svc._events.publish.call_args_list
            if c[0][0].type == "task.started"
        ]
        assert started_events == []

    @patch("src.app.usecases.task_execute.LeaseRefresher")
    def test_start_raises_after_max_cas_retries_exhausted(self, mock_refresher_cls):
        """
        If update_if_version keeps conflicting through all MAX_UPDATE_RETRIES
        attempts in _start_task_with_retry, a RuntimeError propagates to the
        generic except handler, which marks the task FAILED.
        """
        svc = _make_service()
        # Every load returns a fresh ASSIGNED task (status never changes)
        svc._task_repo.load.side_effect = lambda tid: _make_task(task_id=tid)
        # CAS writes always conflict
        svc._task_repo.update_if_version.return_value = False
        svc._registry.get.return_value = AgentProps(agent_id="worker-1", name="W1")
        svc._git.create_workspace.return_value = "/tmp/ws"

        # execute() catches the RuntimeError from _start_task_with_retry and
        # calls _handle_failure — so execute() itself returns normally.
        svc.execute("t-001", "p-001", "worker-1")

        # A task.failed event must have been published
        failed_events = [
            c for c in svc._events.publish.call_args_list
            if c[0][0].type == "task.failed"
        ]
        assert len(failed_events) == 1
        assert "Version conflict" in failed_events[0][0][0].payload["reason"]

    # ------------------------------------------------------------------
    # _handle_failure — skips when task already left active state
    # ------------------------------------------------------------------

    def test_handle_failure_skips_when_task_not_active(self):
        """
        _handle_failure is a no-op when the task object passed to it is already
        in a non-active state (e.g. the reconciler cancelled it between the runtime
        completing and the failure handler running).

        Tested directly to avoid the _validate_assignment guard in execute().
        """
        from src.domain.value_objects.task import TaskResult
        svc = _make_service()

        # Build a task that has gone past active state
        task = _make_task()
        task.start()   # ASSIGNED → IN_PROGRESS
        task.complete(TaskResult(commit_sha="abc"))  # IN_PROGRESS → SUCCEEDED

        svc._handle_failure(task, "worker-1", "some transient error")

        # No persistence write and no event should be emitted
        svc._task_repo.update_if_version.assert_not_called()
        svc._events.publish.assert_not_called()

    # ------------------------------------------------------------------
    # _build_env — non-string runtime_config values are JSON-encoded
    # ------------------------------------------------------------------

    @patch("src.app.usecases.task_execute.LeaseRefresher")
    def test_non_string_runtime_config_values_are_json_encoded(self, mock_refresher_cls):
        """runtime_config entries with non-string values (int, bool, dict)
        must be serialised to JSON strings in the subprocess environment."""
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

    @patch("src.app.usecases.task_execute.LeaseRefresher")
    def test_terminate_session_exception_is_swallowed(self, mock_refresher_cls):
        """An exception from runtime.terminate_session() in the finally block
        must not propagate — cleanup should be best-effort."""
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

        # Must not raise — terminate_session error is silently swallowed
        svc.execute("t-001", "p-001", "worker-1")

        assert task.status == TaskStatus.SUCCEEDED

    # ------------------------------------------------------------------
    # _start_task_with_retry — assignment stolen mid-CAS
    # ------------------------------------------------------------------

    @patch("src.app.usecases.task_execute.LeaseRefresher")
    def test_start_raises_when_assignment_changes_during_cas(self, mock_refresher_cls):
        """
        If the task is still ASSIGNED on reload but the assignment.agent_id
        changed (reassigned to another worker), the CAS loop raises RuntimeError.
        That error is caught by the generic except handler which marks it FAILED.
        """
        initial = _make_task()   # passes _validate_assignment

        def make_reassigned():
            t = _make_task()
            t.assignment = Assignment(agent_id="other-worker")  # different agent
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

        # execute() catches the RuntimeError via the generic handler
        svc.execute("t-001", "p-001", "worker-1")

        # A task.failed event must have been emitted
        failed = [c for c in svc._events.publish.call_args_list
                  if c[0][0].type == "task.failed"]
        assert len(failed) == 1
        assert "assignment changed" in failed[0][0][0].payload["reason"]

    # ------------------------------------------------------------------
    # _handle_failure — when the failure handler itself errors
    # ------------------------------------------------------------------

    def test_handle_failure_swallows_its_own_exception(self):
        """
        If update_if_version raises inside _handle_failure, the exception
        must be swallowed — the outer finally block must still run.
        """
        svc = _make_service()
        task = _make_task()   # ASSIGNED → active, so failure path executes
        task.start()          # IN_PROGRESS — also active

        svc._task_repo.update_if_version.side_effect = RuntimeError("disk full")

        # Must not propagate — _handle_failure has try/except Exception
        svc._handle_failure(task, "worker-1", "some reason")

        # No event should have been published (update failed before publish)
        svc._events.publish.assert_not_called()
