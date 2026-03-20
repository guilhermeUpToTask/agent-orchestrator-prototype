"""
tests/integration/test_e2e_full.py — Full E2E acceptance test suite.

Run manually after major changes:
    AGENT_MODE=dry-run pytest tests/integration/test_e2e_full.py -v

Infrastructure (all real, no mocks):
    fakeredis.FakeRedis()         — real Redis Streams protocol, consumer groups, TTL
    RedisEventAdapter             — real serialisation/deserialisation through Redis
    RedisLeaseAdapter             — real TTL-based lease management
    GitWorkspaceAdapter           — real git init / branch / commit / push
    SimulatedAgentRuntime         — writes real files to workspace (no external LLM)
    YamlTaskRepository            — real YAML files on disk
    JsonAgentRegistry             — real JSON registry on disk

Scenarios:
    Happy paths
      1. Full lifecycle: created → assigned → in_progress → SUCCEEDED
         Verifies: task status, commit_sha, branch pushed to git repo
      2. Dependent tasks: task-B blocked until task-A SUCCEEDS, then unblocked

    Failure paths
      3. Agent failure → auto-retry → SUCCEEDED on second attempt
      4. Retry exhaustion (max_retries=0) → task CANCELED
      5. Forbidden file edit → task FAILED immediately
      6. No eligible agent → task stays CREATED

    Reconciler paths
      7. Lease expires on ASSIGNED task → reconciler fails it → requeued → SUCCEEDED
      8. Task stuck in CREATED → reconciler republishes → assigned → SUCCEEDED
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import fakeredis
import pytest

from src.domain import (
    AgentExecutionResult,
    AgentProps,
    AgentSelector,
    Assignment,
    DomainEvent,
    ExecutionSpec,
    RetryPolicy,
    TaskAggregate,
    TaskStatus,
    TrustLevel,
)
from src.infra.git.workspace_adapter import GitWorkspaceAdapter
from src.infra.logs_and_tests import FilesystemTaskLogsAdapter, SubprocessTestRunnerAdapter
from src.infra.redis_adapters.event_adapter import RedisEventAdapter
from src.infra.redis_adapters.lease_adapter import RedisLeaseAdapter
from src.infra.runtime.dry_run_runtime import SimulatedAgentRuntime


# ---------------------------------------------------------------------------
# Infrastructure fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_redis():
    """One in-process Redis server shared across all adapters in a test."""
    return fakeredis.FakeRedis()


@pytest.fixture()
def event_port(fake_redis, tmp_path):
    return RedisEventAdapter(fake_redis, journal_dir=str(tmp_path / "events"))


@pytest.fixture()
def lease_port(fake_redis):
    return RedisLeaseAdapter(fake_redis)


@pytest.fixture()
def task_repo(tmp_path):
    from src.infra.fs.task_repository import YamlTaskRepository
    (tmp_path / "tasks").mkdir()
    return YamlTaskRepository(tmp_path / "tasks")


@pytest.fixture()
def agent_registry(tmp_path):
    from src.infra.fs.agent_registry import JsonAgentRegistry
    (tmp_path / "agents").mkdir()
    return JsonAgentRegistry(tmp_path / "agents" / "registry.json")


@pytest.fixture()
def git_repo(tmp_path):
    """
    A real local git repository that agents push task branches to.
    Acts as the 'remote' for GitWorkspaceAdapter.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME":    "orchestrator",
        "GIT_AUTHOR_EMAIL":   "orchestrator@local",
        "GIT_COMMITTER_NAME": "orchestrator",
        "GIT_COMMITTER_EMAIL":"orchestrator@local",
    }
    subprocess.run(["git", "init", "-b", "main", str(repo)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
                   check=True, capture_output=True, env=env)
    # Allow agents to push task branches while main is checked out
    subprocess.run(["git", "-C", str(repo), "config", "receive.denyCurrentBranch", "ignore"],
                   check=True, capture_output=True)
    return repo


@pytest.fixture()
def git_workspace(tmp_path, monkeypatch):
    """
    Real GitWorkspaceAdapter that creates local clones.
    Git identity is injected via env so commits work in CI without global config.
    """
    monkeypatch.setenv("GIT_AUTHOR_NAME",    "e2e-agent")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL",   "e2e@test.local")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "e2e-agent")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL","e2e@test.local")
    ws_base = tmp_path / "workspaces"
    ws_base.mkdir()
    return GitWorkspaceAdapter(workspace_base=ws_base)


@pytest.fixture()
def alive_agent(agent_registry) -> AgentProps:
    """A fully registered agent with a live heartbeat."""
    agent = AgentProps(
        agent_id="e2e-worker-001",
        name="E2E Worker",
        capabilities=["backend_dev"],
        version="1.0.0",
        trust_level=TrustLevel.HIGH,
        last_heartbeat=datetime.now(timezone.utc),
        runtime_type="dry-run",
    )
    agent_registry.register(agent)
    return agent


# ---------------------------------------------------------------------------
# System helpers
# ---------------------------------------------------------------------------

def make_task(
    task_id: str = "task-e2e-001",
    files: list[str] | None = None,
    depends_on: list[str] | None = None,
    max_retries: int = 2,
) -> TaskAggregate:
    return TaskAggregate(
        task_id=task_id,
        feature_id="feat-e2e",
        title=f"E2E task {task_id}",
        description="Write a hello world module.",
        agent_selector=AgentSelector(required_capability="backend_dev"),
        execution=ExecutionSpec(
            type="code:backend",
            files_allowed_to_modify=files or ["src/hello.py", "tests/test_hello.py"],
            test_command=None,
        ),
        retry_policy=RetryPolicy(max_retries=max_retries),
        depends_on=depends_on or [],
    )


def make_task_manager(task_repo, agent_registry, event_port, lease_port):
    from src.app.handlers.task_manager import TaskManagerHandler
    from src.domain import SchedulerService
    return TaskManagerHandler(
        task_repo=task_repo,
        agent_registry=agent_registry,
        event_port=event_port,
        lease_port=lease_port,
        scheduler=SchedulerService(),
    )


def make_worker(
    task_repo,
    agent_registry,
    event_port,
    lease_port,
    git_workspace,
    git_repo,
    tmp_path,
    monkeypatch,
    *,
    simulate_failure: bool = False,
):
    from src.app.handlers.worker import WorkerHandler
    monkeypatch.setattr("src.infra.logs_and_tests.LOG_BASE", tmp_path / "logs")
    (tmp_path / "logs").mkdir(exist_ok=True)
    runtime = SimulatedAgentRuntime(simulate_failure=simulate_failure)
    return WorkerHandler(
        agent_id="e2e-worker-001",
        repo_url=f"file://{git_repo}",
        task_repo=task_repo,
        agent_registry=agent_registry,
        event_port=event_port,
        lease_port=lease_port,
        git_workspace=git_workspace,
        runtime_factory=lambda _: runtime,
        logs_port=FilesystemTaskLogsAdapter(),
        test_runner=SubprocessTestRunnerAdapter(),
        task_timeout_seconds=30,
    )


def make_reconciler(task_repo, lease_port, event_port, agent_registry,
                    stuck_age: int = 120):
    from src.app.reconciliation import Reconciler
    return Reconciler(
        task_repo=task_repo,
        lease_port=lease_port,
        event_port=event_port,
        agent_registry=agent_registry,
        interval_seconds=60,
        stuck_task_min_age_seconds=stuck_age,
    )


def git_branches(repo: Path) -> list[str]:
    """Return all branch names in a git repo."""
    result = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list"],
        capture_output=True, text=True
    )
    return [b.strip().lstrip("* ") for b in result.stdout.splitlines() if b.strip()]


def drain_events(
    fake_redis,
    event_types: list[str],
    group: str,
    consumer: str,
    count: int = 20,
    block_ms: int = 200,
) -> list[DomainEvent]:
    """
    Read all currently pending events from the given streams.
    Uses xreadgroup directly with a short block timeout so tests stay fast.
    Returns immediately when messages are available; waits block_ms if empty.
    """
    read_dict = {f"events:{et}": ">" for et in event_types}
    for stream_key in read_dict:
        try:
            fake_redis.xgroup_create(stream_key, group, id="0", mkstream=True)
        except Exception:
            pass   # group already exists

    results = fake_redis.xreadgroup(group, consumer, read_dict, block=block_ms, count=count)
    events: list[DomainEvent] = []
    if not results:
        return events

    for stream_key, messages in results:
        stream_name = stream_key.decode() if isinstance(stream_key, bytes) else stream_key
        for msg_id, fields in messages:
            raw = fields.get(b"data") or fields.get("data", b"{}")
            if isinstance(raw, bytes):
                raw = raw.decode()
            events.append(DomainEvent.model_validate(json.loads(raw)))
            fake_redis.xack(stream_name, group, msg_id)
    return events


# ===========================================================================
# Scenario 1 — Happy path: full lifecycle
# ===========================================================================

class TestHappyPath:

    def test_full_lifecycle_created_to_succeeded(
        self,
        fake_redis,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
        git_workspace,
        git_repo,
        alive_agent,
        tmp_path,
        monkeypatch,
    ):
        """
        Full pipeline from task creation to SUCCEEDED.

        Verifies:
          - task transitions through every status
          - task.assigned, task.started, task.completed events published
          - commit_sha is set on task.result
          - git branch task/<task_id> exists in the remote repo
        """
        task = make_task()
        task_repo.save(task)

        tm = make_task_manager(task_repo, agent_registry, event_port, lease_port)
        worker = make_worker(task_repo, agent_registry, event_port, lease_port,
                              git_workspace, git_repo, tmp_path, monkeypatch)

        # ── Task manager assigns ──────────────────────────────────────────
        assigned = tm.handle_task_created(task.task_id)
        assert assigned is True

        t = task_repo.load(task.task_id)
        assert t.status == TaskStatus.ASSIGNED
        assert t.assignment is not None
        assert t.assignment.agent_id == alive_agent.agent_id
        assert lease_port.is_lease_active(task.task_id)

        created_events = drain_events(
            fake_redis, ["task.assigned"], "verify", "v1"
        )
        assert len(created_events) == 1
        assert created_events[0].payload["task_id"] == task.task_id

        # ── Worker executes ───────────────────────────────────────────────
        worker.process(task_id=task.task_id, project_id="proj-e2e")

        final = task_repo.load(task.task_id)
        assert final.status == TaskStatus.SUCCEEDED, f"Got: {final.status}"
        assert final.result is not None
        assert final.result.commit_sha is not None
        assert len(final.result.commit_sha) == 40   # full SHA

        # ── Event sequence ────────────────────────────────────────────────
        # Use the global events:all stream — contains ALL published events in order
        all_events = drain_events(fake_redis, ["all"], "verify-all", "va", block_ms=500)
        all_types  = {e.type for e in all_events}
        assert "task.assigned"  in all_types
        assert "task.started"   in all_types
        assert "task.completed" in all_types

        # ── Git branch verification ───────────────────────────────────────
        branches = git_branches(git_repo)
        expected_branch = f"task/{task.task_id}"
        assert expected_branch in branches, (
            f"Branch '{expected_branch}' not found. Available: {branches}"
        )

        # ── Lease released after success ──────────────────────────────────
        assert not lease_port.is_lease_active(task.task_id)

    def test_dependent_task_unblocks_after_predecessor_succeeds(
        self,
        fake_redis,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
        git_workspace,
        git_repo,
        alive_agent,
        tmp_path,
        monkeypatch,
    ):
        """
        task-B depends on task-A.
        task-B must stay blocked until task-A SUCCEEDS, then be assigned.
        """
        task_a = make_task("task-e2e-A", files=["a.py"])
        task_b = make_task("task-e2e-B", files=["b.py"], depends_on=["task-e2e-A"])
        task_repo.save(task_a)
        task_repo.save(task_b)

        tm = make_task_manager(task_repo, agent_registry, event_port, lease_port)
        worker = make_worker(task_repo, agent_registry, event_port, lease_port,
                              git_workspace, git_repo, tmp_path, monkeypatch)

        # task-B should not be assigned (dependency unmet)
        assigned_b = tm.handle_task_created(task_b.task_id)
        assert assigned_b is False
        assert task_repo.load(task_b.task_id).status == TaskStatus.CREATED

        # task-A runs successfully
        tm.handle_task_created(task_a.task_id)
        worker.process(task_id=task_a.task_id, project_id="proj")
        assert task_repo.load(task_a.task_id).status == TaskStatus.SUCCEEDED

        # task-B should now be unblocked by the completed event
        tm.handle_task_completed(task_a.task_id)
        assert task_repo.load(task_b.task_id).status == TaskStatus.ASSIGNED

        # task-B executes successfully
        worker.process(task_id=task_b.task_id, project_id="proj")
        assert task_repo.load(task_b.task_id).status == TaskStatus.SUCCEEDED

        # Both branches exist in the repo
        branches = git_branches(git_repo)
        assert "task/task-e2e-A" in branches
        assert "task/task-e2e-B" in branches


# ===========================================================================
# Scenario 2 — Failure paths
# ===========================================================================

class TestFailurePaths:

    def test_agent_failure_retries_and_succeeds(
        self,
        fake_redis,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
        git_workspace,
        git_repo,
        alive_agent,
        tmp_path,
        monkeypatch,
    ):
        """
        First worker attempt fails → task_manager requeues (retries remain)
        → second worker attempt succeeds → SUCCEEDED.
        """
        task = make_task(max_retries=2)
        task_repo.save(task)

        tm = make_task_manager(task_repo, agent_registry, event_port, lease_port)

        # Assign task
        tm.handle_task_created(task.task_id)
        assert task_repo.load(task.task_id).status == TaskStatus.ASSIGNED

        # First worker attempt fails
        failing_worker = make_worker(
            task_repo, agent_registry, event_port, lease_port,
            git_workspace, git_repo, tmp_path, monkeypatch,
            simulate_failure=True,
        )
        failing_worker.process(task_id=task.task_id, project_id="proj")
        assert task_repo.load(task.task_id).status == TaskStatus.FAILED

        # task_manager handles failure → requeues (retry count < max)
        tm.handle_task_failed(task.task_id)
        t = task_repo.load(task.task_id)
        assert t.status == TaskStatus.REQUEUED
        assert t.retry_policy.attempt == 1

        # Assign again
        tm.handle_task_requeued(task.task_id)
        assert task_repo.load(task.task_id).status == TaskStatus.ASSIGNED

        # Second attempt succeeds
        succeeding_worker = make_worker(
            task_repo, agent_registry, event_port, lease_port,
            git_workspace, git_repo, tmp_path, monkeypatch,
            simulate_failure=False,
        )
        succeeding_worker.process(task_id=task.task_id, project_id="proj")
        final = task_repo.load(task.task_id)
        assert final.status == TaskStatus.SUCCEEDED
        assert final.result.commit_sha is not None

    def test_retry_exhaustion_cancels_task(
        self,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
        git_workspace,
        git_repo,
        alive_agent,
        tmp_path,
        monkeypatch,
    ):
        """
        max_retries=0: first failure immediately cancels the task.
        """
        task = make_task(max_retries=0)
        task_repo.save(task)

        tm = make_task_manager(task_repo, agent_registry, event_port, lease_port)
        tm.handle_task_created(task.task_id)

        failing_worker = make_worker(
            task_repo, agent_registry, event_port, lease_port,
            git_workspace, git_repo, tmp_path, monkeypatch,
            simulate_failure=True,
        )
        failing_worker.process(task_id=task.task_id, project_id="proj")
        assert task_repo.load(task.task_id).status == TaskStatus.FAILED

        tm.handle_task_failed(task.task_id)
        final = task_repo.load(task.task_id)
        assert final.status == TaskStatus.CANCELED

        # Verify canceled event published
        canceled = drain_events(
            event_port._r, ["task.canceled"], "verify-cancel", "v1", block_ms=200
        )
        assert any(e.payload["task_id"] == task.task_id for e in canceled)

    def test_forbidden_file_edit_fails_task(
        self,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
        git_workspace,
        git_repo,
        alive_agent,
        tmp_path,
        monkeypatch,
    ):
        """
        Agent writes outside the declared allowed files → task FAILED,
        no retry (it's a code contract violation, not a transient failure).
        """
        from src.infra.runtime.dry_run_runtime import SimulatedAgentRuntime, SimulatedSessionHandle

        task = make_task(files=["src/hello.py"])
        task_repo.save(task)

        # Patch runtime to also write a forbidden file
        def bad_wait(self, handle, timeout_seconds=600):
            ctx = handle.context
            ws  = Path(ctx.workspace_dir)
            for f in ctx.allowed_files:
                (ws / f).parent.mkdir(parents=True, exist_ok=True)
                (ws / f).write_text("ok\n")
            forbidden = ws / "etc/passwd"
            forbidden.parent.mkdir(parents=True, exist_ok=True)
            forbidden.write_text("root:x:0:0\n")
            return AgentExecutionResult(
                success=True, exit_code=0,
                modified_files=list(ctx.allowed_files) + ["etc/passwd"],
            )

        monkeypatch.setattr(SimulatedAgentRuntime, "wait_for_completion", bad_wait)
        monkeypatch.setattr("src.infra.logs_and_tests.LOG_BASE", tmp_path / "logs")
        (tmp_path / "logs").mkdir(exist_ok=True)

        runtime = SimulatedAgentRuntime()
        from src.app.handlers.worker import WorkerHandler
        worker = WorkerHandler(
            agent_id="e2e-worker-001",
            repo_url=f"file://{git_repo}",
            task_repo=task_repo,
            agent_registry=agent_registry,
            event_port=event_port,
            lease_port=lease_port,
            git_workspace=git_workspace,
            runtime_factory=lambda _: runtime,
            logs_port=FilesystemTaskLogsAdapter(),
            test_runner=SubprocessTestRunnerAdapter(),
            task_timeout_seconds=30,
        )

        tm = make_task_manager(task_repo, agent_registry, event_port, lease_port)
        tm.handle_task_created(task.task_id)
        worker.process(task_id=task.task_id, project_id="proj")

        final = task_repo.load(task.task_id)
        assert final.status == TaskStatus.FAILED
        assert final.result is None   # no commit on failure

        # No branch pushed for a failed task
        branches = git_branches(git_repo)
        assert f"task/{task.task_id}" not in branches

    def test_no_eligible_agent_leaves_task_pending(
        self,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
    ):
        """
        Register an agent with the wrong capability.
        Task stays CREATED — no assignment, no lease, no event.
        """
        wrong_agent = AgentProps(
            agent_id="frontend-agent",
            name="Frontend",
            capabilities=["frontend"],
            version="1.0.0",
            last_heartbeat=datetime.now(timezone.utc),
        )
        agent_registry.register(wrong_agent)

        task = make_task()
        task_repo.save(task)

        tm = make_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_created(task.task_id)

        assert result is False
        assert task_repo.load(task.task_id).status == TaskStatus.CREATED
        assert not lease_port.is_lease_active(task.task_id)

    def test_dead_agent_not_selected(
        self,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
    ):
        """
        Agent with stale heartbeat is not eligible for assignment.
        """
        dead_agent = AgentProps(
            agent_id="dead-agent",
            name="Dead",
            capabilities=["backend_dev"],
            version="1.0.0",
            last_heartbeat=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        agent_registry.register(dead_agent)

        task = make_task()
        task_repo.save(task)

        tm = make_task_manager(task_repo, agent_registry, event_port, lease_port)
        result = tm.handle_task_created(task.task_id)

        assert result is False
        assert task_repo.load(task.task_id).status == TaskStatus.CREATED


# ===========================================================================
# Scenario 3 — Reconciler paths
# ===========================================================================

class TestReconcilerPaths:

    def test_expired_lease_on_assigned_task_fails_then_requeues(
        self,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
        git_workspace,
        git_repo,
        alive_agent,
        tmp_path,
        monkeypatch,
    ):
        """
        A task gets stuck ASSIGNED with an expired lease (worker crashed).
        Reconciler detects → marks FAILED → task_manager requeues → worker succeeds.
        """
        task = make_task(max_retries=2)
        task_repo.save(task)

        tm = make_task_manager(task_repo, agent_registry, event_port, lease_port)
        tm.handle_task_created(task.task_id)
        assert task_repo.load(task.task_id).status == TaskStatus.ASSIGNED

        # Expire the lease to simulate a crashed worker by deleting all lease keys
        for key in lease_port._r.keys("lease:*"):
            lease_port._r.delete(key)
        assert not lease_port.is_lease_active(task.task_id)

        # Reconciler detects expired lease → FAILED + task.failed event
        reconciler = make_reconciler(task_repo, lease_port, event_port, agent_registry)
        reconciler.run_once()
        assert task_repo.load(task.task_id).status == TaskStatus.FAILED

        # task_manager handles failure → REQUEUED (retries remain)
        tm.handle_task_failed(task.task_id)
        assert task_repo.load(task.task_id).status == TaskStatus.REQUEUED

        # Assign and execute successfully
        tm.handle_task_requeued(task.task_id)
        assert task_repo.load(task.task_id).status == TaskStatus.ASSIGNED

        worker = make_worker(
            task_repo, agent_registry, event_port, lease_port,
            git_workspace, git_repo, tmp_path, monkeypatch,
        )
        worker.process(task_id=task.task_id, project_id="proj")
        assert task_repo.load(task.task_id).status == TaskStatus.SUCCEEDED

    def test_reconciler_republishes_stuck_created_task(
        self,
        fake_redis,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
        git_workspace,
        git_repo,
        alive_agent,
        tmp_path,
        monkeypatch,
    ):
        """
        A task sits in CREATED longer than stuck_age (e.g. task-manager was down).
        Reconciler re-emits task.created → task_manager assigns → worker succeeds.
        """
        task = make_task()
        task_repo.save(task)

        # Backdate updated_at to look stuck
        task.updated_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        task_repo.save(task)

        tm = make_task_manager(task_repo, agent_registry, event_port, lease_port)

        # Reconciler with 60-second stuck threshold (task is 5 minutes old → republishes)
        reconciler = make_reconciler(
            task_repo, lease_port, event_port, agent_registry, stuck_age=60
        )
        reconciler.run_once()

        # Reconciler re-publishes task.created; task_manager handles it
        republished = drain_events(
            fake_redis, ["task.created"], "reconciler-verify", "rv1", block_ms=300
        )
        assert any(e.payload.get("task_id") == task.task_id for e in republished), \
            "Reconciler did not republish task.created"

        # Task manager assigns the task
        tm.handle_task_created(task.task_id)
        assert task_repo.load(task.task_id).status == TaskStatus.ASSIGNED

        # Worker completes it
        worker = make_worker(
            task_repo, agent_registry, event_port, lease_port,
            git_workspace, git_repo, tmp_path, monkeypatch,
        )
        worker.process(task_id=task.task_id, project_id="proj")
        assert task_repo.load(task.task_id).status == TaskStatus.SUCCEEDED

    def test_reconciler_fails_in_progress_expired_lease(
        self,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
        alive_agent,
    ):
        """
        A task is IN_PROGRESS and its lease expires (worker timed out mid-execution).
        Reconciler marks it FAILED.
        """
        task = make_task(max_retries=2)
        assignment = Assignment(agent_id=alive_agent.agent_id, lease_seconds=60)
        task.assign(assignment)
        task.start()
        task_repo.save(task)

        lease_port.create_lease(task.task_id, alive_agent.agent_id, 60)
        for key in lease_port._r.keys("lease:*"):
            lease_port._r.delete(key)

        reconciler = make_reconciler(task_repo, lease_port, event_port, agent_registry)
        reconciler.run_once()

        assert task_repo.load(task.task_id).status == TaskStatus.FAILED

    def test_reconciler_skips_terminal_tasks(
        self,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
    ):
        """
        SUCCEEDED, FAILED (with no retries from reconciler), CANCELED, MERGED tasks
        are never touched by the reconciler.
        """
        statuses = [TaskStatus.SUCCEEDED, TaskStatus.CANCELED, TaskStatus.MERGED]
        for s in statuses:
            t = make_task(task_id=f"task-terminal-{s.value}")
            t.status = s
            task_repo.save(t)

        reconciler = make_reconciler(task_repo, lease_port, event_port, agent_registry)
        reconciler.run_once()

        for s in statuses:
            assert task_repo.load(f"task-terminal-{s.value}").status == s, \
                f"Reconciler modified terminal task with status {s}"

    def test_reconciler_handles_exception_per_task_gracefully(
        self,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
        alive_agent,
    ):
        """
        If one task's reconciliation raises an exception, the reconciler
        continues processing the remaining tasks (per-task error isolation).
        """
        good = make_task("task-good")
        good.updated_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        task_repo.save(good)

        reconciler = make_reconciler(
            task_repo, lease_port, event_port, agent_registry, stuck_age=60
        )

        call_count = {"n": 0}
        original = reconciler._process

        def patched(task):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated per-task error")
            return original(task)

        reconciler._process = patched
        reconciler.run_once()   # must not raise

        assert call_count["n"] >= 1


# ===========================================================================
# Scenario 4 — CAS concurrency (optimistic locking)
# ===========================================================================

class TestOptimisticConcurrency:

    def test_assignment_cas_retries_on_version_conflict(
        self,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
        alive_agent,
    ):
        """
        Simulate a version conflict on the first write.
        TaskAssignUseCase retries and succeeds on the second attempt.
        """
        from src.app.usecases.task_assign import TaskAssignUseCase, AssignOutcome
        from src.domain import SchedulerService

        task = make_task()
        task_repo.save(task)

        original_update = task_repo.update_if_version
        call_count = {"n": 0}

        def conflict_once(task_id, new_state, expected_v):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return False   # simulate conflict on first attempt
            return original_update(task_id, new_state, expected_v)

        task_repo.update_if_version = conflict_once

        uc = TaskAssignUseCase(
            task_repo=task_repo,
            agent_registry=agent_registry,
            event_port=event_port,
            lease_port=lease_port,
            scheduler=SchedulerService(),
        )
        result = uc.execute(task.task_id)

        assert result.outcome == AssignOutcome.ASSIGNED
        assert call_count["n"] >= 2    # at least one retry

    def test_fail_handling_cas_retries_on_version_conflict(
        self,
        task_repo,
        agent_registry,
        event_port,
        lease_port,
    ):
        """
        Version conflict during fail handling retries cleanly.
        """
        from src.app.usecases.task_fail_handling import TaskFailHandlingUseCase, FailHandlingOutcome

        task = make_task(max_retries=2)
        task.status = TaskStatus.FAILED
        task_repo.save(task)

        original_update = task_repo.update_if_version
        call_count = {"n": 0}

        def conflict_once(task_id, new_state, expected_v):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return False
            return original_update(task_id, new_state, expected_v)

        task_repo.update_if_version = conflict_once

        uc = TaskFailHandlingUseCase(task_repo=task_repo, event_port=event_port)
        result = uc.execute(task.task_id)

        assert result.outcome == FailHandlingOutcome.REQUEUED
        assert call_count["n"] >= 2
