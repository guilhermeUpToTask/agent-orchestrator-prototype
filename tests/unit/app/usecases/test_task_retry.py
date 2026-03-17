"""
tests/unit/app/usecases/test_task_retry.py — TaskRetryUseCase tests.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, call

from src.app.usecases.task_retry import TaskRetryUseCase, TaskRetryResult
from src.core.models import (
    AgentSelector,
    Assignment,
    ExecutionSpec,
    TaskAggregate,
    TaskStatus,
)


def _make_task(task_id: str = "t-001", status: TaskStatus = TaskStatus.FAILED) -> TaskAggregate:
    return TaskAggregate(
        task_id=task_id,
        feature_id="feat-001",
        title="T",
        description="D",
        agent_selector=AgentSelector(required_capability="code"),
        execution=ExecutionSpec(type="code"),
        status=status,
        assignment=Assignment(agent_id="worker-1") if status == TaskStatus.ASSIGNED else None,
    )


class TestTaskRetryUseCase:
    def setup_method(self):
        self.repo = MagicMock()
        self.events = MagicMock()
        self.usecase = TaskRetryUseCase(task_repo=self.repo, event_port=self.events)

    # ------------------------------------------------------------------
    # Happy paths — all non-MERGED statuses
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("status", [
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
        TaskStatus.SUCCEEDED,
        TaskStatus.CREATED,
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.REQUEUED,
    ])
    def test_force_requeue_any_non_merged_status(self, status):
        task = _make_task(status=status)
        self.repo.load.return_value = task

        result = self.usecase.execute("t-001")

        assert task.status == TaskStatus.REQUEUED
        assert task.assignment is None
        assert isinstance(result, TaskRetryResult)
        assert result.previous_status == status
        self.repo.save.assert_called_once_with(task)
        self.events.publish.assert_called_once()
        event = self.events.publish.call_args[0][0]
        assert event.type == "task.requeued"
        assert event.payload["task_id"] == "t-001"

    def test_result_records_previous_status(self):
        task = _make_task(status=TaskStatus.CANCELED)
        self.repo.load.return_value = task

        result = self.usecase.execute("t-001")

        assert result.previous_status == TaskStatus.CANCELED

    # ------------------------------------------------------------------
    # Not found
    # ------------------------------------------------------------------

    def test_raises_key_error_when_task_missing(self):
        self.repo.load.side_effect = KeyError("t-missing")

        with pytest.raises(KeyError):
            self.usecase.execute("t-missing")

        self.repo.save.assert_not_called()
        self.events.publish.assert_not_called()

    # ------------------------------------------------------------------
    # MERGED is blocked by domain invariant
    # ------------------------------------------------------------------

    def test_raises_value_error_for_merged_task(self):
        task = _make_task(status=TaskStatus.MERGED)
        self.repo.load.return_value = task

        with pytest.raises(ValueError, match="MERGED"):
            self.usecase.execute("t-001")

        self.repo.save.assert_not_called()
        self.events.publish.assert_not_called()

    # ------------------------------------------------------------------
    # Actor is threaded through to the aggregate history
    # ------------------------------------------------------------------

    def test_custom_actor_appears_in_history(self):
        task = _make_task(status=TaskStatus.FAILED)
        self.repo.load.return_value = task

        self.usecase.execute("t-001", actor="ops-team")

        history_actors = [e.actor for e in task.history]
        assert "ops-team" in history_actors

    def test_retry_does_not_increment_retry_counter(self):
        task = _make_task(status=TaskStatus.FAILED)
        original_attempt = task.retry_policy.attempt
        self.repo.load.return_value = task

        self.usecase.execute("t-001")

        # force_requeue must not touch the retry counter
        assert task.retry_policy.attempt == original_attempt
