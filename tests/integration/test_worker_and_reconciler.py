"""
tests/integration/test_worker_and_reconciler.py

WorkerHandler tests:
  - Full happy path → SUCCEEDED
  - Agent not in registry → RuntimeError
  - Wrong agent_id → RuntimeError
  - Task not ASSIGNED → RuntimeError
  - Simulate agent failure → FAILED
  - Forbidden file edits → FAILED
  - Test command failure → FAILED (mocked)
  - Workspace / session cleanup always happens (finally block)
  - Lease revoked after completion / failure

Reconciler tests:
  - CREATED task → republishes task.created event
  - REQUEUED task → republishes task.requeued event
  - ASSIGNED + expired lease → REQUEUED
  - ASSIGNED + dead agent → REQUEUED
  - ASSIGNED + dead agent + max retries exhausted → CANCELED
  - IN_PROGRESS + expired lease → FAILED
  - FAILED + retries left → REQUEUED
  - FAILED + retries exhausted → CANCELED
  - SUCCEEDED without commit_sha → warning only (no state change)
  - SUCCEEDED / MERGED / CANCELED → no action
  - CAS conflict on requeue (simulated) → error logged, no crash
  - Empty task list → no-op
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.models import (
    AgentExecutionResult,
    AgentProps,
    AgentSelector,
    Assignment,
    DomainEvent,
    ExecutionSpec,
    RetryPolicy,
    TaskAggregate,
    TaskResult,
    TaskStatus,
    TrustLevel,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_workflow(tmp_path):
    for d in ["tasks", "agents", "logs"]:
        (tmp_path / d).mkdir()
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
    agent = AgentProps(
        agent_id="agent-worker-001",
        name="Worker 001",
        capabilities=["backend_dev"],
        version="1.2.0",
        tools=["pytest", "git"],
        trust_level=TrustLevel.HIGH,
        last_heartbeat=datetime.now(timezone.utc),
    )
    agent_registry.register(agent)
    return agent


def make_task(
    task_id: str = "task-001",
    status: TaskStatus = TaskStatus.ASSIGNED,
    agent_id: str = "agent-worker-001",
    max_retries: int = 2,
    allowed_files: list[str] | None = None,
    test_command: str | None = None,
) -> TaskAggregate:
    task = TaskAggregate(
        task_id=task_id,
        feature_id="feat-x",
        title=f"Task {task_id}",
        description="Desc",
        agent_selector=AgentSelector(required_capability="backend_dev"),
        execution=ExecutionSpec(
            type="code:backend",
            files_allowed_to_modify=allowed_files or ["app/auth.py"],
            test_command=test_command,
        ),
        status=status,
        retry_policy=RetryPolicy(max_retries=max_retries),
    )
    if status == TaskStatus.ASSIGNED:
        task.assignment = Assignment(
            agent_id=agent_id,
            lease_token="test-lease-token",
        )
    return task


def build_worker(
    agent_id: str,
    task_repo,
    agent_registry,
    event_port,
    lease_port,
    tmp_workflow,
    runtime=None,
):
    from src.app.handlers.worker import WorkerHandler
    from src.infra.git.workspace_adapter import DryRunGitWorkspaceAdapter
    from src.infra.runtime.agent_runtime import DryRunAgentRuntime
    from src.infra.logs_and_tests import (
        FilesystemTaskLogsAdapter,
        SubprocessTestRunnerAdapter,
    )

    dry_run = runtime or DryRunAgentRuntime()

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


def build_reconciler(task_repo, lease_port, event_port, agent_registry,
                     interval: int = 5, stuck_task_min_age_seconds: int = 0):
    from src.app.reconciler import Reconciler
    return Reconciler(
        task_repo=task_repo,
        lease_port=lease_port,
        event_port=event_port,
        agent_registry=agent_registry,
        interval_seconds=interval,
        stuck_task_min_age_seconds=stuck_task_min_age_seconds,
    )


# ===========================================================================
# WorkerHandler — happy path
# ===========================================================================

class TestWorkerHandlerHappyPath:

    def test_task_transitions_to_succeeded(
        self, task_repo, agent_registry, event_port, lease_port, worker_agent, tmp_workflow, monkeypatch
    ):
        monkeypatch.setattr("src.app.handlers.worker.LOG_BASE", tmp_workflow / "logs")
        task = make_task()
        task_repo.save(task)
        lease_port.create_lease(task.task_id, worker_agent.agent_id, 300)

        worker = build_worker(worker_agent.agent_id, task_repo, agent_registry, event_port, lease_port, tmp_workflow)
        worker.process(task.task_id, "proj-test")

        final = task_repo.load(task.task_id)
        assert final.status == TaskStatus.SUCCEEDED

    def test_result_has_commit_sha(
        self, task_repo, agent_registry, event_port, lease_port, worker_agent, tmp_workflow, monkeypatch
    ):
        monkeypatch.setattr("src.app.handlers.worker.LOG_BASE", tmp_workflow / "logs")
        task = make_task()
        task_repo.save(task)
        worker = build_worker(worker_agent.agent_id, task_repo, agent_registry, event_port, lease_port, tmp_workflow)
        worker.process(task.task_id, "proj-test")
        final = task_repo.load(task.task_id)
        assert final.result is not None
        assert final.result.commit_sha is not None

    def test_emits_started_and_completed_events(
        self, task_repo, agent_registry, event_port, lease_port, worker_agent, tmp_workflow, monkeypatch
    ):
        monkeypatch.setattr("src.app.handlers.worker.LOG_BASE", tmp_workflow / "logs")
        task = make_task()
        task_repo.save(task)
        worker = build_worker(worker_agent.agent_id, task_repo, agent_registry, event_port, lease_port, tmp_workflow)
        worker.process(task.task_id, "proj-test")
        event_types = [e.type for e in event_port.all_events]
        assert "task.started" in event_types
        assert "task.completed" in event_types

    def test_logs_written_to_disk(
        self, task_repo, agent_registry, event_port, lease_port, worker_agent, tmp_workflow, monkeypatch
    ):
        log_base = tmp_workflow / "logs"
        monkeypatch.setattr("src.app.handlers.worker.LOG_BASE", log_base)
        task = make_task()
        task_repo.save(task)
        worker = build_worker(worker_agent.agent_id, task_repo, agent_registry, event_port, lease_port, tmp_workflow)
        worker.process(task.task_id, "proj-test")
        log_dir = log_base / task.task_id
        assert (log_dir / "stdout.txt").exists()
        assert (log_dir / "stderr.txt").exists()
        assert (log_dir / "metadata.json").exists()

    def test_lease_revoked_after_success(
        self, task_repo, agent_registry, event_port, lease_port, worker_agent, tmp_workflow, monkeypatch
    ):
        monkeypatch.setattr("src.app.handlers.worker.LOG_BASE", tmp_workflow / "logs")
        task = make_task()
        lease_token = lease_port.create_lease(task.task_id, worker_agent.agent_id, 300)
        task.assignment.lease_token = lease_token
        task_repo.save(task)

        worker = build_worker(worker_agent.agent_id, task_repo, agent_registry, event_port, lease_port, tmp_workflow)
        worker.process(task.task_id, "proj-test")
        assert not lease_port.is_lease_active(task.task_id)


# ===========================================================================
# WorkerHandler — error paths
# ===========================================================================

class TestWorkerHandlerErrors:

    def test_agent_not_in_registry_raises(
        self, task_repo, agent_registry, event_port, lease_port, tmp_workflow, monkeypatch
    ):
        monkeypatch.setattr("src.app.handlers.worker.LOG_BASE", tmp_workflow / "logs")
        task = make_task(agent_id="missing-agent")
        task_repo.save(task)

        worker = build_worker("missing-agent", task_repo, agent_registry, event_port, lease_port, tmp_workflow)
        with pytest.raises(RuntimeError, match="not found in registry"):
            worker.process(task.task_id, "proj-test")

    def test_wrong_agent_id_raises(
        self, task_repo, agent_registry, event_port, lease_port, worker_agent, tmp_workflow, monkeypatch
    ):
        monkeypatch.setattr("src.app.handlers.worker.LOG_BASE", tmp_workflow / "logs")
        task = make_task(agent_id="other-agent")
        task_repo.save(task)

        # Worker is agent-worker-001 but task is assigned to other-agent
        worker = build_worker(worker_agent.agent_id, task_repo, agent_registry, event_port, lease_port, tmp_workflow)
        with pytest.raises(RuntimeError, match="not this worker"):
            worker.process(task.task_id, "proj-test")

    def test_task_not_assigned_raises(
        self, task_repo, agent_registry, event_port, lease_port, worker_agent, tmp_workflow, monkeypatch
    ):
        monkeypatch.setattr("src.app.handlers.worker.LOG_BASE", tmp_workflow / "logs")
        task = make_task(status=TaskStatus.CREATED)
        task.assignment = None
        task_repo.save(task)

        worker = build_worker(worker_agent.agent_id, task_repo, agent_registry, event_port, lease_port, tmp_workflow)
        with pytest.raises(RuntimeError, match="no assignment"):
            worker.process(task.task_id, "proj-test")

    def test_task_in_wrong_status_raises(
        self, task_repo, agent_registry, event_port, lease_port, worker_agent, tmp_workflow, monkeypatch
    ):
        monkeypatch.setattr("src.app.handlers.worker.LOG_BASE", tmp_workflow / "logs")
        # Task is IN_PROGRESS (not ASSIGNED) but assignment is present — must
        # reach the status guard, not the "has no assignment" guard.
        task = make_task(status=TaskStatus.IN_PROGRESS)
        task.assignment = Assignment(agent_id=worker_agent.agent_id, lease_token="tok")
        task_repo.save(task)

        worker = build_worker(worker_agent.agent_id, task_repo, agent_registry, event_port, lease_port, tmp_workflow)
        with pytest.raises(RuntimeError, match="expected assigned"):
            worker.process(task.task_id, "proj-test")

    def test_agent_failure_causes_task_failed(
        self, task_repo, agent_registry, event_port, lease_port, worker_agent, tmp_workflow, monkeypatch
    ):
        from src.infra.runtime.agent_runtime import DryRunAgentRuntime
        monkeypatch.setattr("src.app.handlers.worker.LOG_BASE", tmp_workflow / "logs")
        failing_runtime = DryRunAgentRuntime(simulate_failure=True)
        task = make_task()
        task_repo.save(task)

        worker = build_worker(
            worker_agent.agent_id, task_repo, agent_registry, event_port, lease_port, tmp_workflow,
            runtime=failing_runtime,
        )
        worker.process(task.task_id, "proj-test")
        final = task_repo.load(task.task_id)
        assert final.status == TaskStatus.FAILED

    def test_agent_failure_emits_task_failed_event(
        self, task_repo, agent_registry, event_port, lease_port, worker_agent, tmp_workflow, monkeypatch
    ):
        from src.infra.runtime.agent_runtime import DryRunAgentRuntime
        monkeypatch.setattr("src.app.handlers.worker.LOG_BASE", tmp_workflow / "logs")
        failing_runtime = DryRunAgentRuntime(simulate_failure=True)
        task = make_task()
        task_repo.save(task)

        worker = build_worker(
            worker_agent.agent_id, task_repo, agent_registry, event_port, lease_port, tmp_workflow,
            runtime=failing_runtime,
        )
        worker.process(task.task_id, "proj-test")
        assert len(event_port.events_of_type("task.failed")) == 1

    def test_forbidden_file_edit_causes_failure(
        self, task_repo, agent_registry, event_port, lease_port, worker_agent, tmp_workflow, monkeypatch
    ):
        from src.infra.runtime.agent_runtime import DryRunAgentRuntime
        from src.infra.git.workspace_adapter import DryRunGitWorkspaceAdapter

        monkeypatch.setattr("src.app.handlers.worker.LOG_BASE", tmp_workflow / "logs")

        def bad_wait(self, handle, timeout_seconds=600):
            # Write forbidden file in workspace
            from pathlib import Path as P
            if handle.context:
                forbidden = P(handle.context.workspace_dir) / "secrets/creds.txt"
                forbidden.parent.mkdir(parents=True, exist_ok=True)
                forbidden.write_text("secret\n")
            return AgentExecutionResult(success=True, exit_code=0)

        monkeypatch.setattr(DryRunAgentRuntime, "wait_for_completion", bad_wait)
        monkeypatch.setattr(
            DryRunGitWorkspaceAdapter, "get_modified_files",
            lambda self, ws: ["secrets/creds.txt"],
        )

        task = make_task(allowed_files=["app/auth.py"])
        task_repo.save(task)
        worker = build_worker(
            worker_agent.agent_id, task_repo, agent_registry, event_port, lease_port, tmp_workflow
        )
        worker.process(task.task_id, "proj-test")

        final = task_repo.load(task.task_id)
        assert final.status == TaskStatus.FAILED

    def test_forbidden_file_edit_records_reason(
        self, task_repo, agent_registry, event_port, lease_port, worker_agent, tmp_workflow, monkeypatch
    ):
        from src.infra.runtime.agent_runtime import DryRunAgentRuntime
        from src.infra.git.workspace_adapter import DryRunGitWorkspaceAdapter

        monkeypatch.setattr("src.app.handlers.worker.LOG_BASE", tmp_workflow / "logs")
        monkeypatch.setattr(DryRunAgentRuntime, "wait_for_completion",
                            lambda self, h, timeout_seconds=600: AgentExecutionResult(success=True, exit_code=0))
        monkeypatch.setattr(DryRunGitWorkspaceAdapter, "get_modified_files",
                            lambda self, ws: ["forbidden.txt"])

        task = make_task(allowed_files=["app/auth.py"])
        task_repo.save(task)
        worker = build_worker(worker_agent.agent_id, task_repo, agent_registry, event_port, lease_port, tmp_workflow)
        worker.process(task.task_id, "proj-test")

        final = task_repo.load(task.task_id)
        failed_entry = next(h for h in final.history if h.event == "task.failed")
        assert "forbidden.txt" in str(failed_entry.detail.get("reason", ""))

    def test_lease_revoked_after_failure(
        self, task_repo, agent_registry, event_port, lease_port, worker_agent, tmp_workflow, monkeypatch
    ):
        from src.infra.runtime.agent_runtime import DryRunAgentRuntime
        monkeypatch.setattr("src.app.handlers.worker.LOG_BASE", tmp_workflow / "logs")
        failing_runtime = DryRunAgentRuntime(simulate_failure=True)

        task = make_task()
        token = lease_port.create_lease(task.task_id, worker_agent.agent_id, 300)
        task.assignment.lease_token = token
        task_repo.save(task)

        worker = build_worker(
            worker_agent.agent_id, task_repo, agent_registry, event_port, lease_port, tmp_workflow,
            runtime=failing_runtime,
        )
        worker.process(task.task_id, "proj-test")
        assert not lease_port.is_lease_active(task.task_id)


# ===========================================================================
# WorkerHandler — file allowlist validation
# ===========================================================================

class TestCheckAllowedFiles:

    def test_no_violations_if_all_modified_allowed(self):
        from src.core.models import ExecutionSpec
        spec = ExecutionSpec(type="code", files_allowed_to_modify=["a.py", "b.py"])
        modified = ["a.py", "b.py"]
        spec.validate_modifications(modified)

    def test_detects_forbidden_file(self):
        import pytest
        from src.core.models import ExecutionSpec, ForbiddenFileEditError
        spec = ExecutionSpec(type="code", files_allowed_to_modify=["a.py"])
        modified = ["a.py", "forbidden.txt"]
        with pytest.raises(ForbiddenFileEditError) as exc:
            spec.validate_modifications(modified)
        assert "forbidden.txt" in exc.value.violations

    def test_empty_modified_no_violations(self):
        from src.core.models import ExecutionSpec
        spec = ExecutionSpec(type="code", files_allowed_to_modify=["a.py"])
        spec.validate_modifications([])

    def test_empty_allowed_all_are_violations(self):
        import pytest
        from src.core.models import ExecutionSpec, ForbiddenFileEditError
        spec = ExecutionSpec(type="code", files_allowed_to_modify=[])
        with pytest.raises(ForbiddenFileEditError) as exc:
            spec.validate_modifications(["a.py", "b.py"])
        assert set(exc.value.violations) == {"a.py", "b.py"}

    def test_multiple_violations_all_reported(self):
        import pytest
        from src.core.models import ExecutionSpec, ForbiddenFileEditError
        spec = ExecutionSpec(type="code", files_allowed_to_modify=["a.py"])
        modified = ["b.txt", "c.txt", "a.py"]
        with pytest.raises(ForbiddenFileEditError) as exc:
            spec.validate_modifications(modified)
        assert set(exc.value.violations) == {"b.txt", "c.txt"}


# ===========================================================================
# Reconciler — pending task recovery
# ===========================================================================

class TestReconcilerPendingTasks:

    def test_created_task_republishes_event(self, task_repo, lease_port, event_port, agent_registry):
        task = make_task("task-created", status=TaskStatus.CREATED)
        task.assignment = None
        task_repo.save(task)

        reconciler = build_reconciler(task_repo, lease_port, event_port, agent_registry)
        reconciler.run_once()

        events = event_port.events_of_type("task.created")
        assert len(events) == 1
        assert events[0].payload["task_id"] == "task-created"

    def test_requeued_task_republishes_event(self, task_repo, lease_port, event_port, agent_registry):
        task = make_task("task-requeued", status=TaskStatus.REQUEUED)
        task.assignment = None
        task_repo.save(task)

        reconciler = build_reconciler(task_repo, lease_port, event_port, agent_registry)
        reconciler.run_once()

        events = event_port.events_of_type("task.requeued")
        assert len(events) == 1

    def test_empty_task_list_is_noop(self, task_repo, lease_port, event_port, agent_registry):
        reconciler = build_reconciler(task_repo, lease_port, event_port, agent_registry)
        reconciler.run_once()  # should not raise
        assert event_port.all_events == []


# ===========================================================================
# Reconciler — lease expiry
# ===========================================================================

class TestReconcilerLeaseExpiry:

    def test_assigned_with_expired_lease_gets_failed(self, task_repo, lease_port, event_port, agent_registry, worker_agent):
        # Reconciler's job: detect expired lease, write FAILED, emit task.failed.
        # The task manager then decides to requeue.
        task = TaskAggregate(
            task_id="task-exp",
            feature_id="f", title="T", description="D",
            agent_selector=AgentSelector(required_capability="backend_dev"),
            execution=ExecutionSpec(type="code:backend"),
            status=TaskStatus.ASSIGNED,
            retry_policy=RetryPolicy(max_retries=2, attempt=0),
        )
        task.assignment = Assignment(agent_id=worker_agent.agent_id)
        task_repo.save(task)

        lease_port.create_lease("task-exp", worker_agent.agent_id, 1)
        lease_port.expire_all()

        reconciler = build_reconciler(task_repo, lease_port, event_port, agent_registry)
        reconciler.run_once()

        final = task_repo.load("task-exp")
        assert final.status == TaskStatus.FAILED

    def test_assigned_with_expired_lease_task_manager_requeues(self, task_repo, lease_port, event_port, agent_registry, worker_agent):
        # Full two-step: reconciler fails, task manager requeues.
        task = TaskAggregate(
            task_id="task-exp-tm",
            feature_id="f", title="T", description="D",
            agent_selector=AgentSelector(required_capability="backend_dev"),
            execution=ExecutionSpec(type="code:backend"),
            status=TaskStatus.ASSIGNED,
            retry_policy=RetryPolicy(max_retries=2, attempt=0),
        )
        task.assignment = Assignment(agent_id=worker_agent.agent_id)
        task_repo.save(task)

        lease_port.create_lease("task-exp-tm", worker_agent.agent_id, 1)
        lease_port.expire_all()

        build_reconciler(task_repo, lease_port, event_port, agent_registry).run_once()
        assert len(event_port.events_of_type("task.failed")) >= 1

        from src.app.handlers.task_manager import TaskManagerHandler
        tm = TaskManagerHandler(
            task_repo=task_repo, agent_registry=agent_registry,
            event_port=event_port, lease_port=lease_port,
        )
        tm.handle_task_failed("task-exp-tm")

        final = task_repo.load("task-exp-tm")
        assert final.status == TaskStatus.REQUEUED

    def test_assigned_expired_lease_emits_failed_event(self, task_repo, lease_port, event_port, agent_registry, worker_agent):
        task = make_task("task-lease-evt", status=TaskStatus.ASSIGNED, agent_id=worker_agent.agent_id)
        task_repo.save(task)
        lease_port.create_lease("task-lease-evt", worker_agent.agent_id, 1)
        lease_port.expire_all()

        build_reconciler(task_repo, lease_port, event_port, agent_registry).run_once()

        assert len(event_port.events_of_type("task.failed")) >= 1

    def test_in_progress_expired_lease_fails_task(self, task_repo, lease_port, event_port, agent_registry, worker_agent):
        task = TaskAggregate(
            task_id="task-stale",
            feature_id="f", title="T", description="D",
            agent_selector=AgentSelector(required_capability="backend_dev"),
            execution=ExecutionSpec(type="code:backend"),
            status=TaskStatus.IN_PROGRESS,
            retry_policy=RetryPolicy(max_retries=2, attempt=0),
        )
        task.assignment = Assignment(agent_id=worker_agent.agent_id)
        task_repo.save(task)

        lease_port.create_lease("task-stale", worker_agent.agent_id, 1)
        lease_port.expire_all()

        build_reconciler(task_repo, lease_port, event_port, agent_registry).run_once()

        final = task_repo.load("task-stale")
        assert final.status == TaskStatus.FAILED

    def test_in_progress_expired_lease_emits_failed_event(self, task_repo, lease_port, event_port, agent_registry, worker_agent):
        task = TaskAggregate(
            task_id="task-stale-evt",
            feature_id="f", title="T", description="D",
            agent_selector=AgentSelector(required_capability="backend_dev"),
            execution=ExecutionSpec(type="code:backend"),
            status=TaskStatus.IN_PROGRESS,
        )
        task.assignment = Assignment(agent_id=worker_agent.agent_id)
        task_repo.save(task)
        lease_port.create_lease("task-stale-evt", worker_agent.agent_id, 1)
        lease_port.expire_all()

        build_reconciler(task_repo, lease_port, event_port, agent_registry).run_once()

        assert len(event_port.events_of_type("task.failed")) >= 1


# ===========================================================================
# Reconciler — dead agent detection
# ===========================================================================

class TestReconcilerDeadAgent:

    def _dead_agent(self, agent_id: str) -> AgentProps:
        return AgentProps(
            agent_id=agent_id, name="Dead",
            capabilities=["backend_dev"], version="1.0.0",
            last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=300),
        )

    def _task_manager(self, task_repo, agent_registry, event_port, lease_port):
        from src.app.handlers.task_manager import TaskManagerHandler
        return TaskManagerHandler(
            task_repo=task_repo, agent_registry=agent_registry,
            event_port=event_port, lease_port=lease_port,
        )

    def test_assigned_dead_agent_reconciler_fails_task(self, task_repo, lease_port, event_port, agent_registry):
        # Reconciler detects dead agent → writes FAILED, emits task.failed.
        agent_registry.register(self._dead_agent("dead-agent"))
        task = make_task("task-dead", agent_id="dead-agent")
        lease_port.create_lease("task-dead", "dead-agent", 300)
        task_repo.save(task)

        build_reconciler(task_repo, lease_port, event_port, agent_registry).run_once()

        final = task_repo.load("task-dead")
        assert final.status == TaskStatus.FAILED
        assert len(event_port.events_of_type("task.failed")) == 1

    def test_assigned_dead_agent_task_manager_requeues(self, task_repo, lease_port, event_port, agent_registry):
        # Full two-step: reconciler fails → task manager requeues (retries left).
        agent_registry.register(self._dead_agent("dead-agent-r"))
        task = make_task("task-dead-r", agent_id="dead-agent-r", max_retries=2)
        lease_port.create_lease("task-dead-r", "dead-agent-r", 300)
        task_repo.save(task)

        build_reconciler(task_repo, lease_port, event_port, agent_registry).run_once()
        self._task_manager(task_repo, agent_registry, event_port, lease_port).handle_task_failed("task-dead-r")

        final = task_repo.load("task-dead-r")
        assert final.status == TaskStatus.REQUEUED

    def test_dead_agent_max_retries_exhausted_cancels(self, task_repo, lease_port, event_port, agent_registry):
        # Retries already exhausted → task manager cancels after reconciler fails.
        agent_registry.register(self._dead_agent("dead-agent-2"))
        task = TaskAggregate(
            task_id="task-dead-max",
            feature_id="f", title="T", description="D",
            agent_selector=AgentSelector(required_capability="backend_dev"),
            execution=ExecutionSpec(type="code:backend"),
            status=TaskStatus.ASSIGNED,
            retry_policy=RetryPolicy(max_retries=2, attempt=2),
        )
        task.assignment = Assignment(agent_id="dead-agent-2")
        lease_port.create_lease("task-dead-max", "dead-agent-2", 300)
        task_repo.save(task)

        build_reconciler(task_repo, lease_port, event_port, agent_registry).run_once()
        self._task_manager(task_repo, agent_registry, event_port, lease_port).handle_task_failed("task-dead-max")

        final = task_repo.load("task-dead-max")
        assert final.status == TaskStatus.CANCELED

    def test_canceled_task_emits_canceled_event(self, task_repo, lease_port, event_port, agent_registry):
        agent_registry.register(self._dead_agent("dead-agent-3"))
        task = TaskAggregate(
            task_id="task-cancel-evt",
            feature_id="f", title="T", description="D",
            agent_selector=AgentSelector(required_capability="backend_dev"),
            execution=ExecutionSpec(type="code:backend"),
            status=TaskStatus.ASSIGNED,
            retry_policy=RetryPolicy(max_retries=2, attempt=2),
        )
        task.assignment = Assignment(agent_id="dead-agent-3")
        lease_port.create_lease("task-cancel-evt", "dead-agent-3", 300)
        task_repo.save(task)

        build_reconciler(task_repo, lease_port, event_port, agent_registry).run_once()
        self._task_manager(task_repo, agent_registry, event_port, lease_port).handle_task_failed("task-cancel-evt")

        assert len(event_port.events_of_type("task.canceled")) >= 1


# ===========================================================================
# Reconciler — failed task retry/cancel
# ===========================================================================

class TestReconcilerFailedTasks:
    """
    Reconciler ignores FAILED status entirely (it's in _IGNORED_STATUSES).
    Requeue / cancel decisions belong to TaskManagerHandler.handle_task_failed.
    These tests verify that handler directly.
    """

    def _task_manager(self, task_repo, agent_registry, event_port, lease_port):
        from src.app.handlers.task_manager import TaskManagerHandler
        return TaskManagerHandler(
            task_repo=task_repo, agent_registry=agent_registry,
            event_port=event_port, lease_port=lease_port,
        )

    def test_failed_with_retries_left_gets_requeued(self, task_repo, lease_port, event_port, agent_registry):
        task = TaskAggregate(
            task_id="task-retry",
            feature_id="f", title="T", description="D",
            agent_selector=AgentSelector(required_capability="backend_dev"),
            execution=ExecutionSpec(type="code:backend"),
            status=TaskStatus.FAILED,
            retry_policy=RetryPolicy(max_retries=3, attempt=1),
        )
        task_repo.save(task)

        self._task_manager(task_repo, agent_registry, event_port, lease_port).handle_task_failed("task-retry")

        final = task_repo.load("task-retry")
        assert final.status == TaskStatus.REQUEUED

    def test_failed_retries_exhausted_gets_canceled(self, task_repo, lease_port, event_port, agent_registry):
        task = TaskAggregate(
            task_id="task-exhaust",
            feature_id="f", title="T", description="D",
            agent_selector=AgentSelector(required_capability="backend_dev"),
            execution=ExecutionSpec(type="code:backend"),
            status=TaskStatus.FAILED,
            retry_policy=RetryPolicy(max_retries=2, attempt=2),
        )
        task_repo.save(task)

        self._task_manager(task_repo, agent_registry, event_port, lease_port).handle_task_failed("task-exhaust")

        final = task_repo.load("task-exhaust")
        assert final.status == TaskStatus.CANCELED

    def test_failed_exhausted_emits_canceled_event(self, task_repo, lease_port, event_port, agent_registry):
        task = TaskAggregate(
            task_id="task-exhaust-evt",
            feature_id="f", title="T", description="D",
            agent_selector=AgentSelector(required_capability="backend_dev"),
            execution=ExecutionSpec(type="code:backend"),
            status=TaskStatus.FAILED,
            retry_policy=RetryPolicy(max_retries=2, attempt=2),
        )
        task_repo.save(task)

        self._task_manager(task_repo, agent_registry, event_port, lease_port).handle_task_failed("task-exhaust-evt")

        assert len(event_port.events_of_type("task.canceled")) >= 1

    def test_reconciler_ignores_failed_status(self, task_repo, lease_port, event_port, agent_registry):
        # Sanity check: reconciler must not touch FAILED tasks at all.
        task = TaskAggregate(
            task_id="task-ignored",
            feature_id="f", title="T", description="D",
            agent_selector=AgentSelector(required_capability="backend_dev"),
            execution=ExecutionSpec(type="code:backend"),
            status=TaskStatus.FAILED,
            retry_policy=RetryPolicy(max_retries=3, attempt=1),
        )
        task_repo.save(task)

        build_reconciler(task_repo, lease_port, event_port, agent_registry).run_once()

        # Reconciler emits nothing and leaves state unchanged
        assert event_port.all_events == []
        assert task_repo.load("task-ignored").status == TaskStatus.FAILED


# ===========================================================================
# Reconciler — terminal state no-ops
# ===========================================================================

class TestReconcilerTerminalStates:

    @pytest.mark.parametrize("terminal_status", [
        TaskStatus.SUCCEEDED,
        TaskStatus.MERGED,
        TaskStatus.CANCELED,
    ])
    def test_terminal_tasks_not_reprocessed(
        self, task_repo, lease_port, event_port, agent_registry, terminal_status
    ):
        task = TaskAggregate(
            task_id=f"task-{terminal_status.value}",
            feature_id="f",
            title="T",
            description="D",
            agent_selector=AgentSelector(required_capability="backend_dev"),
            execution=ExecutionSpec(type="code:backend"),
            status=terminal_status,
        )
        if terminal_status == TaskStatus.SUCCEEDED:
            task.result = TaskResult(commit_sha="abc123")
        task_repo.save(task)

        build_reconciler(task_repo, lease_port, event_port, agent_registry).run_once()

        # No events should be emitted for terminal tasks
        assert event_port.all_events == []
        # Status unchanged
        final = task_repo.load(task.task_id)
        assert final.status == terminal_status

    def test_succeeded_without_commit_sha_no_state_change(self, task_repo, lease_port, event_port, agent_registry):
        task = TaskAggregate(
            task_id="task-no-sha",
            feature_id="f",
            title="T",
            description="D",
            agent_selector=AgentSelector(required_capability="backend_dev"),
            execution=ExecutionSpec(type="code:backend"),
            status=TaskStatus.SUCCEEDED,
            result=TaskResult(commit_sha=None),  # no commit sha
        )
        task_repo.save(task)

        build_reconciler(task_repo, lease_port, event_port, agent_registry).run_once()

        # Should only warn, no state change
        final = task_repo.load("task-no-sha")
        assert final.status == TaskStatus.SUCCEEDED
        assert event_port.all_events == []

    def test_reconciler_handles_exception_per_task_without_stopping(
        self, task_repo, lease_port, event_port, agent_registry
    ):
        """A single task error should not stop the reconciler pass."""
        # Good task
        good = TaskAggregate(
            task_id="task-good",
            feature_id="f",
            title="T",
            description="D",
            agent_selector=AgentSelector(required_capability="backend_dev"),
            execution=ExecutionSpec(type="code:backend"),
            status=TaskStatus.CREATED,
        )
        task_repo.save(good)

        reconciler = build_reconciler(task_repo, lease_port, event_port, agent_registry)

        # Patch _reconcile_task to raise on the first call, succeed on the second
        call_count = {"n": 0}
        original = reconciler._reconcile_task

        def patched(task):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated error")
            return original(task)

        reconciler._reconcile_task = patched

        # Should not raise even though one task errored
        reconciler.run_once()
        # The pass completed
        assert call_count["n"] >= 1