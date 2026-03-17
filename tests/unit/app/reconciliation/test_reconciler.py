import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
from src.app.reconciliation import Reconciler
from src.core.models import (
    TaskAggregate,
    TaskStatus,
    AgentProps,
    AgentSelector,
    ExecutionSpec,
    DomainEvent,
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
    )


class TestReconciler:
    def setup_method(self):
        self.repo = MagicMock()
        self.lease = MagicMock()
        self.events = MagicMock()
        self.registry = MagicMock()
        self.reconciler = Reconciler(self.repo, self.lease, self.events, self.registry)

    def test_reconcile_stuck_pending(self):
        task = make_task("t1", TaskStatus.CREATED)
        # Mocking updated_at to be old
        task.updated_at = datetime.now(timezone.utc) - timedelta(seconds=200)
        self.repo.list_all.return_value = [task]

        self.reconciler.run_once()

        self.events.publish.assert_called_once()
        assert self.events.publish.call_args[0][0].type == "task.created"

    def test_reconcile_dead_agent(self):
        task = make_task("t1", TaskStatus.ASSIGNED)
        from src.core.models import Assignment

        task.assignment = Assignment(agent_id="a1")
        self.repo.list_all.return_value = [task]
        self.repo.load.return_value = task
        self.repo.update_if_version.return_value = True
        self.registry.get.return_value = AgentProps(
            agent_id="a1",
            name="A1",
            last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=300),
        )

        self.reconciler.run_once()

        # Should fail the task
        assert task.status == TaskStatus.FAILED
        self.events.publish.assert_called_once()
        assert self.events.publish.call_args[0][0].type == "task.failed"

    def test_reconcile_expired_lease_in_progress(self):
        task = make_task("t1", TaskStatus.IN_PROGRESS)
        self.repo.list_all.return_value = [task]
        self.repo.load.return_value = task
        self.repo.update_if_version.return_value = True
        self.lease.is_lease_active.return_value = False

        self.reconciler.run_once()

        assert task.status == TaskStatus.FAILED
        assert "Lease expired" in self.events.publish.call_args[0][0].payload["reason"]
