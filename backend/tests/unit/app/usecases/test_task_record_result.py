"""
tests/unit/app/usecases/test_task_record_result.py — TaskRecordResultUseCase.

The task manager's sole-writer use case for worker execution outcomes.
Covers the transition matrix, idempotency under event redelivery, CAS
conflict retry, and deleted-task tolerance.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.app.usecases.task_record_result import TaskRecordResultUseCase
from src.domain import (
    AgentSelector,
    Assignment,
    ExecutionSpec,
    TaskAggregate,
    TaskResult,
    TaskStatus,
)


def _make_task(
    status: TaskStatus = TaskStatus.ASSIGNED, agent_id: str = "worker-1"
) -> TaskAggregate:
    return TaskAggregate(
        task_id="t-001",
        feature_id="feat-001",
        title="T",
        description="D",
        agent_selector=AgentSelector(required_capability="code"),
        execution=ExecutionSpec(type="code"),
        status=status,
        assignment=Assignment(agent_id=agent_id),
    )


def _result() -> TaskResult:
    return TaskResult(branch="task/t-001", commit_sha="sha-abc", modified_files=["a.py"])


def _usecase(task: TaskAggregate | None):
    repo = MagicMock()
    if task is None:
        repo.load.side_effect = KeyError("gone")
    else:
        repo.load.return_value = task
    repo.update_if_version.return_value = True
    events = MagicMock()
    return TaskRecordResultUseCase(task_repo=repo, event_port=events), repo, events


def _published_types(events) -> list[str]:
    return [c[0][0].type for c in events.publish.call_args_list]


class TestRecordStarted:
    def test_assigned_task_transitions_and_publishes_started(self):
        task = _make_task()
        uc, repo, events = _usecase(task)

        uc.record_started("t-001", "worker-1")

        assert task.status == TaskStatus.IN_PROGRESS
        repo.update_if_version.assert_called_once()
        assert _published_types(events) == ["task.started"]

    def test_redelivery_after_start_is_skipped(self):
        task = _make_task(status=TaskStatus.IN_PROGRESS)
        uc, repo, events = _usecase(task)

        uc.record_started("t-001", "worker-1")

        repo.update_if_version.assert_not_called()
        events.publish.assert_not_called()

    def test_assignment_changed_is_skipped(self):
        task = _make_task(agent_id="someone-else")
        uc, repo, events = _usecase(task)

        uc.record_started("t-001", "worker-1")

        repo.update_if_version.assert_not_called()
        events.publish.assert_not_called()

    def test_cas_conflict_retries_then_succeeds(self):
        task = _make_task()
        uc, repo, events = _usecase(task)
        repo.update_if_version.side_effect = [False, True]
        # Fresh reload per attempt
        repo.load.side_effect = [_make_task(), _make_task()]

        uc.record_started("t-001", "worker-1")

        assert repo.update_if_version.call_count == 2
        assert _published_types(events) == ["task.started"]

    def test_deleted_task_does_not_raise(self):
        uc, repo, events = _usecase(None)
        uc.record_started("t-001", "worker-1")
        events.publish.assert_not_called()


class TestRecordSucceeded:
    def test_in_progress_task_completes_and_publishes_completed(self):
        task = _make_task(status=TaskStatus.IN_PROGRESS)
        uc, repo, events = _usecase(task)

        uc.record_succeeded("t-001", "worker-1", _result())

        assert task.status == TaskStatus.SUCCEEDED
        assert task.result.commit_sha == "sha-abc"
        assert _published_types(events) == ["task.completed"]
        payload = events.publish.call_args[0][0].payload
        assert payload["task_id"] == "t-001"
        assert payload["commit_sha"] == "sha-abc"

    def test_assigned_task_catches_up_start_before_completing(self):
        # execution_succeeded can arrive before execution_started (separate
        # streams) — the start transition is applied as catch-up.
        task = _make_task(status=TaskStatus.ASSIGNED)
        uc, repo, events = _usecase(task)

        uc.record_succeeded("t-001", "worker-1", _result())

        assert task.status == TaskStatus.SUCCEEDED
        repo.update_if_version.assert_called_once()
        assert _published_types(events) == ["task.completed"]

    def test_redelivery_after_success_is_idempotent(self):
        task = _make_task(status=TaskStatus.IN_PROGRESS)
        task.complete(_result())
        uc, repo, events = _usecase(task)

        uc.record_succeeded("t-001", "worker-1", _result())

        repo.update_if_version.assert_not_called()
        events.publish.assert_not_called()

    def test_terminal_task_is_skipped(self):
        task = _make_task(status=TaskStatus.CANCELED)
        uc, repo, events = _usecase(task)

        uc.record_succeeded("t-001", "worker-1", _result())

        repo.update_if_version.assert_not_called()
        events.publish.assert_not_called()


class TestRecordFailed:
    def test_in_progress_task_fails_and_publishes_failed(self):
        task = _make_task(status=TaskStatus.IN_PROGRESS)
        uc, repo, events = _usecase(task)

        uc.record_failed("t-001", "worker-1", "agent exploded")

        assert task.status == TaskStatus.FAILED
        assert task.last_error == "agent exploded"
        assert _published_types(events) == ["task.failed"]
        assert events.publish.call_args[0][0].payload["reason"] == "agent exploded"

    def test_assigned_task_can_fail_directly(self):
        task = _make_task(status=TaskStatus.ASSIGNED)
        uc, repo, events = _usecase(task)

        uc.record_failed("t-001", "worker-1", "never started")

        assert task.status == TaskStatus.FAILED

    def test_redelivery_after_failure_is_idempotent(self):
        task = _make_task(status=TaskStatus.IN_PROGRESS)
        task.fail("first")
        uc, repo, events = _usecase(task)

        uc.record_failed("t-001", "worker-1", "second")

        repo.update_if_version.assert_not_called()
        events.publish.assert_not_called()

    def test_cas_exhaustion_logs_without_raising(self):
        uc, repo, events = _usecase(_make_task(status=TaskStatus.IN_PROGRESS))
        repo.load.side_effect = lambda _tid: _make_task(status=TaskStatus.IN_PROGRESS)
        repo.update_if_version.return_value = False

        uc.record_failed("t-001", "worker-1", "reason")

        assert repo.update_if_version.call_count == 5
        events.publish.assert_not_called()
