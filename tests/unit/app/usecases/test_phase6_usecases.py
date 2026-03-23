"""
tests/unit/app/usecases/test_phase6_usecases.py

Unit tests for Phase 6 use cases:
  TaskAssignUseCase, TaskFailHandlingUseCase, TaskUnblockUseCase
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock


from src.domain import (
    AgentProps, AgentSelector, ExecutionSpec, TaskAggregate, TaskStatus,
)
from src.app.usecases.task_assign import (
    TaskAssignUseCase, AssignOutcome,
)
from src.app.usecases.task_fail_handling import (
    TaskFailHandlingUseCase, FailHandlingOutcome,
)
from src.app.usecases.task_unblock import TaskUnblockUseCase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task(
    task_id: str = "t-001",
    status: TaskStatus = TaskStatus.CREATED,
    depends_on: list[str] | None = None,
) -> TaskAggregate:
    return TaskAggregate(
        task_id=task_id,
        feature_id="feat-x",
        title="T",
        description="D",
        agent_selector=AgentSelector(required_capability="code"),
        execution=ExecutionSpec(type="code"),
        status=status,
        depends_on=depends_on or [],
    )


def _alive_agent(agent_id: str = "a-001") -> AgentProps:
    return AgentProps(
        agent_id=agent_id,
        name="Agent",
        capabilities=["code"],
        last_heartbeat=datetime.now(timezone.utc),
    )


# ===========================================================================
# TaskAssignUseCase
# ===========================================================================

class TestTaskAssignUseCase:

    def setup_method(self):
        self.repo      = MagicMock()
        self.registry  = MagicMock()
        self.events    = MagicMock()
        self.lease     = MagicMock()
        self.scheduler = MagicMock()
        self.uc = TaskAssignUseCase(
            task_repo=self.repo,
            agent_registry=self.registry,
            event_port=self.events,
            lease_port=self.lease,
            scheduler=self.scheduler,
        )

    def _setup_happy_path(self, task):
        self.repo.load.return_value = task
        self.repo.update_if_version.return_value = True
        agent = _alive_agent()
        self.registry.list_agents.return_value = [agent]
        self.scheduler.select_agent.return_value = agent
        self.lease.create_lease.return_value = "lease-token"
        return agent

    # Happy path

    def test_assigns_task_and_returns_assigned_outcome(self):
        task = _task()
        self._setup_happy_path(task)

        result = self.uc.execute("t-001")

        assert result.outcome == AssignOutcome.ASSIGNED
        assert result.agent_id == "a-001"
        assert task.status == TaskStatus.ASSIGNED
        assert task.assignment.lease_token == "lease-token"

    def test_publishes_task_assigned_event(self):
        task = _task()
        self._setup_happy_path(task)
        self.uc.execute("t-001")
        event = self.events.publish.call_args[0][0]
        assert event.type == "task.assigned"
        assert event.payload["agent_id"] == "a-001"

    def test_two_cas_writes_in_order(self):
        task = _task()
        self._setup_happy_path(task)
        self.uc.execute("t-001")
        assert self.repo.update_if_version.call_count == 2

    # Not assignable

    def test_returns_not_assignable_for_wrong_status(self):
        self.repo.load.return_value = _task(status=TaskStatus.IN_PROGRESS)
        result = self.uc.execute("t-001")
        assert result.outcome == AssignOutcome.NOT_ASSIGNABLE
        self.events.publish.assert_not_called()

    # Not found

    def test_returns_not_found_when_task_deleted(self):
        self.repo.load.side_effect = KeyError("t-001")
        result = self.uc.execute("t-001")
        assert result.outcome == AssignOutcome.NOT_FOUND

    # No eligible agent

    def test_returns_no_eligible_agent_when_scheduler_returns_none(self):
        self.repo.load.return_value = _task()
        self.registry.list_agents.return_value = []
        self.scheduler.select_agent.return_value = None
        result = self.uc.execute("t-001")
        assert result.outcome == AssignOutcome.NO_ELIGIBLE_AGENT

    # Deps not met

    def test_returns_deps_not_met_when_dependency_not_succeeded(self):
        task = _task(depends_on=["t-dep"])
        self.repo.load.return_value = task
        self.repo.list_all.return_value = [task]   # dep not in succeeded
        result = self.uc.execute("t-001")
        assert result.outcome == AssignOutcome.DEPS_NOT_MET

    def test_assigns_when_all_deps_succeeded(self):
        dep   = _task("t-dep", TaskStatus.SUCCEEDED)
        task  = _task("t-001", depends_on=["t-dep"])
        self.repo.load.return_value = task
        self.repo.list_all.return_value = [dep, task]
        self.repo.update_if_version.return_value = True
        agent = _alive_agent()
        self.registry.list_agents.return_value = [agent]
        self.scheduler.select_agent.return_value = agent
        self.lease.create_lease.return_value = "tok"

        result = self.uc.execute("t-001")
        assert result.outcome == AssignOutcome.ASSIGNED

    # CAS retry

    def test_retries_on_version_conflict(self):
        tasks_returned = []
        def load_side(tid):
            t = _task(task_id=tid)
            tasks_returned.append(t)
            return t

        self.repo.load.side_effect = load_side
        # First write fails (conflict), then both writes succeed
        self.repo.update_if_version.side_effect = [False, True, True]
        agent = _alive_agent()
        self.registry.list_agents.return_value = [agent]
        self.scheduler.select_agent.return_value = agent
        self.lease.create_lease.return_value = "tok"

        result = self.uc.execute("t-001")
        assert result.outcome == AssignOutcome.ASSIGNED
        assert self.repo.update_if_version.call_count >= 2

    def test_lease_revoked_on_second_write_conflict(self):
        task = _task()
        self.repo.load.return_value = task
        agent = _alive_agent()
        self.registry.list_agents.return_value = [agent]
        self.scheduler.select_agent.return_value = agent
        self.lease.create_lease.return_value = "tok"
        # First write OK, second write fails → lease must be revoked
        self.repo.update_if_version.side_effect = [True, False] + [True, True] * 5

        self.uc.execute("t-001")

        self.lease.revoke_lease.assert_called_with("tok")

    # preloaded_succeeded passthrough

    def test_skips_list_all_when_preloaded_succeeded_provided(self):
        task = _task(depends_on=["t-dep"])
        self.repo.load.return_value = task
        self.repo.update_if_version.return_value = True
        agent = _alive_agent()
        self.registry.list_agents.return_value = [agent]
        self.scheduler.select_agent.return_value = agent
        self.lease.create_lease.return_value = "tok"

        self.uc.execute("t-001", preloaded_succeeded={"t-dep"})

        self.repo.list_all.assert_not_called()


# ===========================================================================
# TaskFailHandlingUseCase
# ===========================================================================

class TestTaskFailHandlingUseCase:

    def setup_method(self):
        self.repo   = MagicMock()
        self.events = MagicMock()
        self.uc = TaskFailHandlingUseCase(task_repo=self.repo, event_port=self.events)

    def test_requeues_when_retries_remain(self):
        task = _task(status=TaskStatus.FAILED)
        task.retry_policy.attempt    = 0
        task.retry_policy.max_retries = 2
        self.repo.load.return_value = task
        self.repo.update_if_version.return_value = True

        result = self.uc.execute("t-001")

        assert result.outcome == FailHandlingOutcome.REQUEUED
        assert task.status == TaskStatus.REQUEUED
        assert task.retry_policy.attempt == 1
        event = self.events.publish.call_args[0][0]
        assert event.type == "task.requeued"

    def test_cancels_when_retries_exhausted(self):
        task = _task(status=TaskStatus.FAILED)
        task.retry_policy.attempt    = 2
        task.retry_policy.max_retries = 2
        self.repo.load.return_value = task
        self.repo.update_if_version.return_value = True

        result = self.uc.execute("t-001")

        assert result.outcome == FailHandlingOutcome.CANCELED
        assert task.status == TaskStatus.CANCELED
        event = self.events.publish.call_args[0][0]
        assert event.type == "task.canceled"

    def test_skips_stale_event(self):
        self.repo.load.return_value = _task(status=TaskStatus.SUCCEEDED)
        result = self.uc.execute("t-001")
        assert result.outcome == FailHandlingOutcome.SKIPPED
        self.events.publish.assert_not_called()

    def test_not_found_when_task_deleted(self):
        self.repo.load.side_effect = KeyError("t-001")
        result = self.uc.execute("t-001")
        assert result.outcome == FailHandlingOutcome.NOT_FOUND
        self.events.publish.assert_not_called()

    def test_retries_on_version_conflict(self):
        self.repo.load.side_effect = lambda tid: _task(tid, TaskStatus.FAILED)
        self.repo.update_if_version.side_effect = [False, True]
        result = self.uc.execute("t-001")
        assert result.outcome == FailHandlingOutcome.REQUEUED
        assert self.repo.update_if_version.call_count == 2

    def test_retries_on_version_conflict_in_cancel_path(self):
        """CAS conflict during the cancel write (retries exhausted branch) retries cleanly."""
        def exhausted_task(tid):
            t = _task(tid, TaskStatus.FAILED)
            t.retry_policy.attempt = 2
            t.retry_policy.max_retries = 2
            return t

        self.repo.load.side_effect = exhausted_task
        # First write (cancel) conflicts, second succeeds
        self.repo.update_if_version.side_effect = [False, True]
        result = self.uc.execute("t-001")
        assert result.outcome == FailHandlingOutcome.CANCELED
        assert self.repo.update_if_version.call_count == 2

    def test_returns_skipped_after_max_cas_retries(self):
        """When update_if_version always conflicts, must give up and return SKIPPED."""
        self.repo.load.side_effect = lambda tid: _task(tid, TaskStatus.FAILED)
        self.repo.update_if_version.return_value = False  # always conflict
        result = self.uc.execute("t-001")
        assert result.outcome == FailHandlingOutcome.SKIPPED
        self.events.publish.assert_not_called()

    def test_no_event_published_on_cas_exhaustion(self):
        self.repo.load.side_effect = lambda tid: _task(tid, TaskStatus.FAILED)
        self.repo.update_if_version.return_value = False
        self.uc.execute("t-001")
        self.events.publish.assert_not_called()


# ===========================================================================
# TaskUnblockUseCase
# ===========================================================================

class TestTaskUnblockUseCase:

    def setup_method(self):
        self.repo   = MagicMock()
        self.assign = MagicMock()
        self.uc = TaskUnblockUseCase(task_repo=self.repo, assign_usecase=self.assign)

    def _assign_result(self, outcome):
        from src.app.usecases.task_assign import TaskAssignResult
        return TaskAssignResult(outcome=outcome, task_id="t-dep")

    def test_unblocks_dependent_when_dep_succeeded(self):
        completed = _task("t-001", TaskStatus.SUCCEEDED)
        dependent = _task("t-dep", TaskStatus.CREATED, depends_on=["t-001"])
        self.repo.list_all.return_value = [completed, dependent]
        self.assign.execute.return_value = self._assign_result(AssignOutcome.ASSIGNED)

        result = self.uc.execute("t-001")

        assert "t-dep" in result.unblocked
        self.assign.execute.assert_called_once()
        # preloaded_succeeded must be passed to avoid O(N²) scans
        call_kwargs = self.assign.execute.call_args
        assert call_kwargs.kwargs.get("preloaded_succeeded") is not None or \
               (len(call_kwargs.args) > 1 and call_kwargs.args[1] is not None)

    def test_skips_task_not_depending_on_completed(self):
        completed  = _task("t-001", TaskStatus.SUCCEEDED)
        unrelated  = _task("t-other", TaskStatus.CREATED, depends_on=["t-xyz"])
        self.repo.list_all.return_value = [completed, unrelated]

        result = self.uc.execute("t-001")

        assert result.count == 0
        self.assign.execute.assert_not_called()

    def test_skips_dependent_with_unmet_deps(self):
        completed  = _task("t-001", TaskStatus.SUCCEEDED)
        dependent  = _task("t-dep", TaskStatus.CREATED, depends_on=["t-001", "t-002"])
        # t-002 not in the task list → not succeeded
        self.repo.list_all.return_value = [completed, dependent]

        result = self.uc.execute("t-001")

        assert result.count == 0
        assert "t-dep" in result.skipped

    def test_multiple_dependents_all_unblocked(self):
        completed = _task("t-001", TaskStatus.SUCCEEDED)
        dep_a     = _task("t-a", TaskStatus.CREATED, depends_on=["t-001"])
        dep_b     = _task("t-b", TaskStatus.CREATED, depends_on=["t-001"])
        self.repo.list_all.return_value = [completed, dep_a, dep_b]
        self.assign.execute.return_value = self._assign_result(AssignOutcome.ASSIGNED)

        result = self.uc.execute("t-001")

        assert result.count == 2
        assert self.assign.execute.call_count == 2

    def test_failed_assign_goes_to_skipped(self):
        completed = _task("t-001", TaskStatus.SUCCEEDED)
        dependent = _task("t-dep", TaskStatus.CREATED, depends_on=["t-001"])
        self.repo.list_all.return_value = [completed, dependent]
        self.assign.execute.return_value = self._assign_result(AssignOutcome.NO_ELIGIBLE_AGENT)

        result = self.uc.execute("t-001")

        assert result.count == 0
        assert "t-dep" in result.skipped

    def test_no_dependents_returns_empty_result(self):
        completed = _task("t-001", TaskStatus.SUCCEEDED)
        self.repo.list_all.return_value = [completed]

        result = self.uc.execute("t-001")

        assert result.count == 0
        assert result.skipped == []


# ===========================================================================
# TaskAssignUseCase — CAS retry exhaustion
# ===========================================================================

class TestTaskAssignCasExhaustion:

    def setup_method(self):
        self.repo      = MagicMock()
        self.registry  = MagicMock()
        self.events    = MagicMock()
        self.lease     = MagicMock()
        self.scheduler = MagicMock()
        self.uc = TaskAssignUseCase(
            task_repo=self.repo,
            agent_registry=self.registry,
            event_port=self.events,
            lease_port=self.lease,
            scheduler=self.scheduler,
        )

    def test_returns_not_assignable_after_max_cas_retries(self):
        """update_if_version always returns False — must give up after MAX_CAS_RETRIES."""
        self.repo.load.side_effect = lambda tid: _task(task_id=tid)
        # Every write attempt conflicts
        self.repo.update_if_version.return_value = False
        agent = _alive_agent()
        self.registry.list_agents.return_value = [agent]
        self.scheduler.select_agent.return_value = agent
        self.lease.create_lease.return_value = "tok"

        result = self.uc.execute("t-001")

        assert result.outcome == AssignOutcome.NOT_ASSIGNABLE
        # Must have retried exactly MAX_CAS_RETRIES times (not looped forever)
        from src.app.usecases.task_assign import MAX_CAS_RETRIES
        assert self.repo.load.call_count == MAX_CAS_RETRIES

    def test_no_event_published_on_cas_exhaustion(self):
        self.repo.load.side_effect = lambda tid: _task(task_id=tid)
        self.repo.update_if_version.return_value = False
        agent = _alive_agent()
        self.registry.list_agents.return_value = [agent]
        self.scheduler.select_agent.return_value = agent
        self.lease.create_lease.return_value = "tok"

        self.uc.execute("t-001")

        self.events.publish.assert_not_called()
