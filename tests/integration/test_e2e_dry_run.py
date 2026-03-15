"""
tests/integration/test_e2e_dry_run.py — Acceptance tests (dry-run mode).

Verifies the full task lifecycle:
  created → assigned → in_progress → succeeded

No external network required. Uses:
  - InMemoryLeaseAdapter
  - InMemoryEventAdapter
  - DryRunAgentRuntime
  - DryRunGitWorkspaceAdapter
  - YamlTaskRepository (real FS in tmp dir)
  - JsonAgentRegistry (real FS in tmp dir)
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.core.models import (
    AgentProps,
    AgentSelector,
    Assignment,
    ExecutionSpec,
    TaskAggregate,
    TaskStatus,
    TrustLevel,
)
from src.core.services import SchedulerService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_workflow(tmp_path):
    """Return a tmp workflow dir with tasks/ and agents/ subdirs."""
    (tmp_path / "tasks").mkdir()
    (tmp_path / "agents").mkdir()
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture()
def task_repo(tmp_workflow):
    from src.infra.fs.task_repository import YamlTaskRepository
    return YamlTaskRepository(tmp_workflow / "tasks")


@pytest.fixture()
def agent_registry(tmp_workflow):
    from src.infra.fs.agent_registry import JsonAgentRegistry
    return JsonAgentRegistry(tmp_workflow / "agents" / "registry.json")


@pytest.fixture()
def event_port():
    from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter
    return InMemoryEventAdapter()


@pytest.fixture()
def lease_port():
    from src.infra.redis_adapters.lease_memory import InMemoryLeaseAdapter
    return InMemoryLeaseAdapter()


@pytest.fixture()
def worker_agent(agent_registry) -> AgentProps:
    from datetime import datetime, timezone
    agent = AgentProps(
        agent_id="agent-worker-001",
        name="Worker 001",
        capabilities=["backend_dev"],
        version="1.2.0",
        tools=["pytest", "git"],
        trust_level=TrustLevel.HIGH,
        last_heartbeat=datetime.now(timezone.utc),   # required for _is_alive()
    )
    agent_registry.register(agent)
    return agent


@pytest.fixture()
def sample_task() -> TaskAggregate:
    return TaskAggregate(
        task_id="task-e2e-001",
        feature_id="feat-auth",
        title="Implement POST /login",
        description="Add login endpoint.",
        agent_selector=AgentSelector(required_capability="backend_dev"),
        execution=ExecutionSpec(
            type="code:backend",
            constraints={"language": "python"},
            files_allowed_to_modify=["app/auth.py", "tests/test_auth.py"],
            test_command=None,   # skip real pytest in CI
            acceptance_criteria=["All tests pass"],
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_task_manager(task_repo, agent_registry, event_port, lease_port):
    from src.app.handlers.task_manager import TaskManagerHandler
    return TaskManagerHandler(
        task_repo=task_repo,
        agent_registry=agent_registry,
        event_port=event_port,
        lease_port=lease_port,
        scheduler=SchedulerService(),
    )


def build_worker(agent_id, task_repo, agent_registry, event_port, lease_port, tmp_workflow):
    from src.app.handlers.worker import WorkerHandler
    from src.infra.git.workspace_adapter import DryRunGitWorkspaceAdapter
    from src.infra.runtime.dry_run_runtime import SimulatedAgentRuntime
    from src.infra.logs_and_tests import FilesystemTaskLogsAdapter, SubprocessTestRunnerAdapter

    # Always use simulated runtime in tests regardless of agent's runtime_type
    dry_run = SimulatedAgentRuntime()

    return WorkerHandler(
        agent_id=agent_id,
        repo_url="file:///dev/null",
        task_repo=task_repo,
        agent_registry=agent_registry,
        event_port=event_port,
        lease_port=lease_port,
        git_workspace=DryRunGitWorkspaceAdapter(),
        runtime_factory=lambda agent_props: dry_run,
        logs_port=FilesystemTaskLogsAdapter(),
        test_runner=SubprocessTestRunnerAdapter(),
        task_timeout_seconds=30,
    )


# ---------------------------------------------------------------------------
# E2E: full lifecycle
# ---------------------------------------------------------------------------

class TestFullLifecycle:

    def test_created_to_succeeded(
        self,
        sample_task,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
        worker_agent,
        tmp_workflow,
        monkeypatch,
    ):
        # Override log dir to use tmp
        monkeypatch.setattr(
            "src.infra.logs_and_tests.LOG_BASE",
            tmp_workflow / "logs",
        )

        # 1. Persist task as created
        task_repo.save(sample_task)
        loaded = task_repo.load(sample_task.task_id)
        assert loaded.status == TaskStatus.CREATED

        # 2. Task Manager assigns
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        assigned = tm.handle_task_created(sample_task.task_id)
        assert assigned is True

        task_after_assign = task_repo.load(sample_task.task_id)
        assert task_after_assign.status == TaskStatus.ASSIGNED
        assert task_after_assign.assignment.agent_id == worker_agent.agent_id

        # Check event emitted
        assign_events = event_port.events_of_type("task.assigned")
        assert len(assign_events) == 1
        assert assign_events[0].payload["task_id"] == sample_task.task_id

        # Check lease created
        assert lease_port.is_lease_active(sample_task.task_id)

        # 3. Worker processes task
        worker = build_worker(
            worker_agent.agent_id,
            task_repo,
            agent_registry,
            event_port,
            lease_port,
            tmp_workflow,
        )
        worker.process(sample_task.task_id, "proj-test")

        # 4. Final state
        final = task_repo.load(sample_task.task_id)
        assert final.status == TaskStatus.SUCCEEDED, f"Unexpected: {final.status}"
        assert final.result is not None
        assert final.result.commit_sha is not None

        # Events emitted in order
        event_types = [e.type for e in event_port.all_events]
        assert "task.assigned" in event_types
        assert "task.started" in event_types
        assert "task.completed" in event_types

    def test_no_eligible_agent_returns_false(
        self, sample_task, task_repo, agent_registry, event_port, lease_port
    ):
        # Register agent without required capability
        agent = AgentProps(
            agent_id="frontend-agent",
            name="Frontend",
            capabilities=["frontend"],
            version="1.0.0",
        )
        agent_registry.register(agent)
        task_repo.save(sample_task)

        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_created(sample_task.task_id)
        assert result is False

        # Task still in CREATED
        assert task_repo.load(sample_task.task_id).status == TaskStatus.CREATED

    def test_forbidden_file_edit_causes_failure(
        self,
        sample_task,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
        worker_agent,
        tmp_workflow,
        monkeypatch,
    ):
        monkeypatch.setattr(
            "src.infra.logs_and_tests.LOG_BASE",
            tmp_workflow / "logs",
        )

        # Monkeypatch simulated-run to also write a forbidden file
        from src.infra.runtime.dry_run_runtime import SimulatedAgentRuntime, SimulatedSessionHandle
        from src.core.models import AgentExecutionResult
        from pathlib import Path as P

        original_wait = SimulatedAgentRuntime.wait_for_completion

        def bad_wait(self, handle, timeout_seconds=600):
            context = handle.context
            ws = P(context.workspace_dir)
            # Write an allowed file
            for f in context.allowed_files:
                full = ws / f
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text("ok\n")
            # Write a FORBIDDEN file
            forbidden = ws / "secrets/passwords.txt"
            forbidden.parent.mkdir(parents=True, exist_ok=True)
            forbidden.write_text("hunter2\n")

            return AgentExecutionResult(
                success=True, exit_code=0,
                modified_files=list(context.allowed_files) + ["secrets/passwords.txt"],
            )

        monkeypatch.setattr(SimulatedAgentRuntime, "wait_for_completion", bad_wait)

        # Also patch git.get_modified_files to return the forbidden file
        from src.infra.git.workspace_adapter import DryRunGitWorkspaceAdapter

        monkeypatch.setattr(
            DryRunGitWorkspaceAdapter,
            "get_modified_files",
            lambda self, ws: list(sample_task.execution.files_allowed_to_modify)
            + ["secrets/passwords.txt"],
        )

        task_repo.save(sample_task)
        tm = build_task_manager(task_repo, agent_registry, event_port, lease_port)
        tm.handle_task_created(sample_task.task_id)

        worker = build_worker(
            worker_agent.agent_id,
            task_repo,
            agent_registry,
            event_port,
            lease_port,
            tmp_workflow,
        )
        worker.process(sample_task.task_id, "proj-test")

        final = task_repo.load(sample_task.task_id)
        assert final.status == TaskStatus.FAILED
        failed_events = event_port.events_of_type("task.failed")
        assert len(failed_events) == 1


# ---------------------------------------------------------------------------
# Reconciler tests
# ---------------------------------------------------------------------------

class TestReconciler:

    def test_requeues_assigned_expired_lease(
        self, sample_task, task_repo, lease_port, event_port, agent_registry
    ):
        # Reconciler's contract: expired lease → task.failed.
        # The task manager then decides to requeue or cancel based on retries.
        sample_task.assign(Assignment(agent_id="agent-001", lease_seconds=1))
        task_repo.save(sample_task)

        lease_port.create_lease(sample_task.task_id, "agent-001", 1)
        lease_port.expire_all()

        from src.app.reconciler import Reconciler
        reconciler = Reconciler(task_repo, lease_port, event_port, agent_registry)
        reconciler.run_once()

        # Reconciler writes FAILED and emits task.failed
        task_after = task_repo.load(sample_task.task_id)
        assert task_after.status == TaskStatus.FAILED

        assert len(event_port.events_of_type("task.failed")) == 1

        # Task manager then handles the failure: retries left → REQUEUED
        from src.app.handlers.task_manager import TaskManagerHandler
        from src.core.services import SchedulerService
        from src.infra.fs.agent_registry import JsonAgentRegistry
        from src.infra.redis_adapters.lease_memory import InMemoryLeaseAdapter
        # Build a fresh task manager (no agent registered here, just verify requeue)
        fresh_lease = InMemoryLeaseAdapter()
        tm = TaskManagerHandler(
            task_repo=task_repo,
            agent_registry=agent_registry,
            event_port=event_port,
            lease_port=fresh_lease,
            scheduler=SchedulerService(),
        )
        tm.handle_task_failed(sample_task.task_id)

        final = task_repo.load(sample_task.task_id)
        assert final.status == TaskStatus.REQUEUED
        assert len(event_port.events_of_type("task.requeued")) == 1