"""
tests/unit/app/usecases/test_phase5_usecases.py

Unit tests for the Phase 5 use cases:
  TaskDeleteUseCase, TaskPruneUseCase,
  AgentRegisterUseCase, ProjectResetUseCase
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from src.domain import (
    AgentProps, AgentSelector, Assignment,
    ExecutionSpec, TaskAggregate, TaskStatus,
)
from src.app.usecases.task_delete  import TaskDeleteUseCase,  TaskDeleteResult
from src.app.usecases.task_prune   import TaskPruneUseCase,   TaskPruneResult
from src.app.usecases.agent_register import AgentRegisterUseCase, AgentRegisterResult
from src.app.usecases.project_reset  import ProjectResetUseCase,  ProjectResetResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task(task_id: str = "t-001", status: TaskStatus = TaskStatus.FAILED) -> TaskAggregate:
    return TaskAggregate(
        task_id=task_id,
        feature_id="feat-x",
        title="T",
        description="D",
        agent_selector=AgentSelector(required_capability="code"),
        execution=ExecutionSpec(type="code"),
        status=status,
    )


def _agent() -> AgentProps:
    return AgentProps(agent_id="a-001", name="Agent", capabilities=["code"])


# ===========================================================================
# TaskDeleteUseCase
# ===========================================================================

class TestTaskDeleteUseCase:

    def setup_method(self):
        self.repo = MagicMock()
        self.uc   = TaskDeleteUseCase(task_repo=self.repo)

    def test_returns_result_with_previous_status(self):
        task = _task(status=TaskStatus.FAILED)
        self.repo.load.return_value = task

        result = self.uc.execute("t-001")

        assert isinstance(result, TaskDeleteResult)
        assert result.task_id == "t-001"
        assert result.previous_status == TaskStatus.FAILED

    def test_calls_repo_delete(self):
        self.repo.load.return_value = _task()
        self.uc.execute("t-001")
        self.repo.delete.assert_called_once_with("t-001")

    def test_raises_key_error_when_not_found(self):
        self.repo.load.side_effect = KeyError("t-001")
        with pytest.raises(KeyError):
            self.uc.execute("t-001")
        self.repo.delete.assert_not_called()

    @pytest.mark.parametrize("status", list(TaskStatus))
    def test_works_for_any_status(self, status):
        self.repo.load.return_value = _task(status=status)
        result = self.uc.execute("t-001")
        assert result.previous_status == status


# ===========================================================================
# TaskPruneUseCase
# ===========================================================================

class TestTaskPruneUseCase:

    def setup_method(self):
        self.repo = MagicMock()
        self.uc   = TaskPruneUseCase(task_repo=self.repo)

    def test_prune_all_when_no_filter(self):
        tasks = [_task("t-001", TaskStatus.FAILED), _task("t-002", TaskStatus.CANCELED)]
        self.repo.list_all.return_value = tasks

        result = self.uc.execute(filter_statuses=None)

        assert result.count == 2
        assert set(result.deleted) == {"t-001", "t-002"}
        assert result.filter_statuses is None
        assert self.repo.delete.call_count == 2

    def test_prune_by_status_filter(self):
        tasks = [
            _task("t-001", TaskStatus.FAILED),
            _task("t-002", TaskStatus.SUCCEEDED),
            _task("t-003", TaskStatus.FAILED),
        ]
        self.repo.list_all.return_value = tasks

        result = self.uc.execute(filter_statuses={TaskStatus.FAILED})

        assert result.count == 2
        assert "t-001" in result.deleted
        assert "t-003" in result.deleted
        assert "t-002" not in result.deleted

    def test_prune_returns_empty_when_no_match(self):
        self.repo.list_all.return_value = [_task("t-001", TaskStatus.SUCCEEDED)]

        result = self.uc.execute(filter_statuses={TaskStatus.FAILED})

        assert result.count == 0
        self.repo.delete.assert_not_called()

    def test_prune_empty_repo_returns_empty(self):
        self.repo.list_all.return_value = []
        result = self.uc.execute()
        assert result.count == 0

    def test_multiple_status_filter(self):
        tasks = [
            _task("t-001", TaskStatus.FAILED),
            _task("t-002", TaskStatus.CANCELED),
            _task("t-003", TaskStatus.CREATED),
        ]
        self.repo.list_all.return_value = tasks

        result = self.uc.execute(
            filter_statuses={TaskStatus.FAILED, TaskStatus.CANCELED}
        )

        assert result.count == 2
        assert "t-003" not in result.deleted


# ===========================================================================
# AgentRegisterUseCase
# ===========================================================================

class TestAgentRegisterUseCase:

    def setup_method(self):
        self.registry = MagicMock()
        self.uc       = AgentRegisterUseCase(agent_registry=self.registry)

    def test_calls_registry_register(self):
        agent = _agent()
        self.uc.execute(agent)
        self.registry.register.assert_called_once_with(agent)

    def test_returns_result_with_agent_id(self):
        agent = _agent()
        result = self.uc.execute(agent)
        assert isinstance(result, AgentRegisterResult)
        assert result.agent_id == "a-001"

    def test_result_reflects_active_flag(self):
        agent = AgentProps(agent_id="a", name="A", active=False)
        result = self.uc.execute(agent)
        assert result.active is False

    def test_result_reflects_runtime_type(self):
        agent = AgentProps(agent_id="a", name="A", runtime_type="claude")
        result = self.uc.execute(agent)
        assert result.runtime_type == "claude"

    def test_any_agent_props_accepted(self):
        agent = AgentProps(
            agent_id="pi-001",
            name="Pi Agent",
            capabilities=["code"],
            runtime_type="pi",
            runtime_config={"backend": "openrouter"},
        )
        result = self.uc.execute(agent)
        assert result.agent_id == "pi-001"
        assert result.runtime_type == "pi"


# ===========================================================================
# ProjectResetUseCase
# ===========================================================================

class TestProjectResetUseCase:

    def setup_method(self):
        self.repo     = MagicMock()
        self.lease    = MagicMock()
        self.registry = MagicMock()
        self.uc = ProjectResetUseCase(
            task_repo=self.repo,
            lease_port=self.lease,
            agent_registry=self.registry,
            repo_url=None,
        )

    def _make_uc_with_tasks(self, tasks):
        self.repo.list_all.return_value = tasks
        self.lease.is_lease_active.return_value = False
        self.registry.list_agents.return_value = []

    def test_deletes_all_tasks(self):
        tasks = [_task("t-001"), _task("t-002")]
        self._make_uc_with_tasks(tasks)

        result = self.uc.execute()

        assert result.tasks_deleted == 2
        assert self.repo.delete.call_count == 2

    def test_releases_active_leases(self):
        tasks = [_task("t-001")]
        self.repo.list_all.return_value = tasks
        self.lease.is_lease_active.return_value = True
        self.lease.get_lease_agent.return_value = "worker-1"
        self.registry.list_agents.return_value = []

        result = self.uc.execute()

        assert result.leases_released == 1
        self.lease.revoke_lease.assert_called_once_with("t-001:worker-1")

    def test_skips_inactive_leases(self):
        tasks = [_task("t-001")]
        self.repo.list_all.return_value = tasks
        self.lease.is_lease_active.return_value = False
        self.registry.list_agents.return_value = []

        result = self.uc.execute()

        assert result.leases_released == 0
        self.lease.revoke_lease.assert_not_called()

    def test_clears_agent_registry(self):
        agents = [_agent(), AgentProps(agent_id="a-002", name="B")]
        self.repo.list_all.return_value = []
        self.registry.list_agents.return_value = agents

        result = self.uc.execute(keep_agents=False)

        assert result.agents_removed == 2
        assert self.registry.deregister.call_count == 2

    def test_keep_agents_skips_registry(self):
        self.repo.list_all.return_value = []
        self.registry.list_agents.return_value = [_agent()]

        result = self.uc.execute(keep_agents=True)

        assert result.agents_removed == 0
        self.registry.deregister.assert_not_called()

    def test_no_errors_on_clean_run(self):
        self.repo.list_all.return_value = []
        self.registry.list_agents.return_value = []
        result = self.uc.execute()
        assert not result.had_errors

    def test_task_repo_failure_recorded_in_errors(self):
        self.repo.list_all.side_effect = RuntimeError("disk full")

        result = self.uc.execute()

        assert result.had_errors
        assert any("tasks" in e for e in result.errors)

    def test_registry_failure_recorded_in_errors(self):
        self.repo.list_all.return_value = []
        self.registry.list_agents.side_effect = RuntimeError("registry unavailable")

        result = self.uc.execute(keep_agents=False)

        assert result.had_errors
        assert any("registry" in e for e in result.errors)

    def test_branch_deletion_skipped_when_no_repo_url(self):
        # repo_url=None means no git push attempts
        self.repo.list_all.return_value = [_task()]
        self.registry.list_agents.return_value = []
        self.lease.is_lease_active.return_value = False

        with patch("subprocess.run") as mock_run:
            result = self.uc.execute()
            mock_run.assert_not_called()

        assert result.branches_deleted == 0

    def test_branch_deletion_with_repo_url(self):
        uc_with_url = ProjectResetUseCase(
            task_repo=self.repo,
            lease_port=self.lease,
            agent_registry=self.registry,
            repo_url="file:///tmp/repo",
        )
        self.repo.list_all.return_value = [_task("t-001")]
        self.lease.is_lease_active.return_value = False
        self.registry.list_agents.return_value = []

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = uc_with_url.execute()

        assert result.branches_deleted == 1
