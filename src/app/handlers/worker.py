"""
src/app/handlers/worker.py — Worker use-case handler.

Per-task execution flow:
  1. Load task, validate assignment
  2. Load agent props → build the correct runtime for this agent
  3. Create ephemeral git workspace
  4. Transition task → in_progress  (CAS with retry — fix #1.1)
  5. Start agent session (AgentRuntimePort)
  6. Send ExecutionContext
  7. Wait for result            (lease is refreshed in background — fix #2.6)
  8. Validate modified files against allowed list
  9. Run acceptance tests       (shell=False for safety — fix #8)
  10. Commit + push on success / fail on error
  11. Persist final state, emit event, cleanup

Fixes applied vs v1:
  #1.1  CAS retry loop around task.start() instead of single-shot raise
  #1.5  _build_env includes runtime_config secrets from AgentProps
  #2.6  Background lease-refresh thread keeps the lease alive during long runs
  #3.2  Subprocess stdout/stderr capped at MAX_OUTPUT_BYTES to prevent OOM
  #3.3  _save_logs wrapped in try/except so a full disk never masks the real error
  #8    _run_tests uses shlex.split + shell=False to eliminate shell-injection risk
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import structlog

from src.core.models import (
    AgentExecutionResult,
    AgentProps,
    DomainEvent,
    ExecutionContext,
    TaskAggregate,
    TaskResult,
    TaskStatus,
)
from src.core.ports import (
    AgentRegistryPort,
    AgentRuntimePort,
    EventPort,
    GitWorkspacePort,
    LeasePort,
    TaskRepositoryPort,
)

log = structlog.get_logger(__name__)

MAX_UPDATE_RETRIES = 5
# Caps stdout/stderr in memory; prevents OOM on runaway test output (fix #3.2)
MAX_OUTPUT_BYTES = 10 * 1024 * 1024          # 10 MB per stream
# Lease-refresh thread settings (fix #2.6)
_LEASE_REFRESH_INTERVAL  = 60               # seconds between refreshes
_LEASE_REFRESH_EXTENSION = 120              # seconds added per refresh

_ORCHESTRATOR_HOME = os.path.abspath(os.getenv("ORCHESTRATOR_HOME", os.path.expanduser("~/.orchestrator")))
LOG_BASE = Path(os.getenv("LOGS_DIR", os.path.join(_ORCHESTRATOR_HOME, "logs")))


class WorkerHandler:
    """
    Executes a single task assignment.
    All side effects (git, agent, persistence) go through ports.

    runtime_factory is a callable that receives AgentProps and returns the
    correct AgentRuntimePort for that agent's runtime_type. This makes the
    system agent-agnostic — Gemini, Claude Code, or any future CLI can be
    swapped per-agent without changing orchestration logic.
    """

    def __init__(
        self,
        agent_id: str,
        repo_url: str,
        task_repo: TaskRepositoryPort,
        agent_registry: AgentRegistryPort,
        event_port: EventPort,
        lease_port: LeasePort,
        git_workspace: GitWorkspacePort,
        runtime_factory: Callable[[AgentProps], AgentRuntimePort],
        task_timeout_seconds: int = 600,
    ) -> None:
        self._agent_id = agent_id
        self._repo_url = repo_url
        self._task_repo = task_repo
        self._registry = agent_registry
        self._events = event_port
        self._lease = lease_port
        self._git = git_workspace
        self._runtime_factory = runtime_factory
        self._timeout = task_timeout_seconds

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process(self, task_id: str, project_id: str) -> None:
        log.info("worker.process_start", task_id=task_id, agent_id=self._agent_id)

        # ----------------------------------------------------------------
        # 1. Load and validate task
        # ----------------------------------------------------------------
        task = self._task_repo.load(task_id)
        self._validate_assignment(task)

        # Capture the lease token now so cleanup can revoke it even if
        # the in-memory task object is mutated by _start_task_with_retry.
        lease_token: str | None = task.assignment.lease_token if task.assignment else None

        # ----------------------------------------------------------------
        # 2. Load agent props and build the runtime for this specific agent
        # ----------------------------------------------------------------
        agent_props = self._registry.get(self._agent_id)
        if agent_props is None:
            raise RuntimeError(f"Agent {self._agent_id} not found in registry")

        runtime = self._runtime_factory(agent_props)
        log.info(
            "worker.runtime_selected",
            task_id=task_id,
            agent_id=self._agent_id,
            runtime_type=agent_props.runtime_type,
        )

        ws_path: str | None = None
        session = None
        lease_refresher: _LeaseRefresher | None = None

        try:
            # ------------------------------------------------------------
            # 3. Create ephemeral workspace
            # ------------------------------------------------------------
            ws_path = self._git.create_workspace(self._repo_url, task_id)
            branch = f"task/{task_id}"
            self._git.checkout_main_and_create_branch(ws_path, branch)
            log.info("worker.workspace_ready", task_id=task_id, path=ws_path)

            # ------------------------------------------------------------
            # 4. Transition task → in_progress (persist-first, with CAS retry)
            #    FIX #1.1: retry up to MAX_UPDATE_RETRIES on version conflict
            #    so a transient reconciler write does not burn a task retry.
            # ------------------------------------------------------------
            task = self._start_task_with_retry(task_id)

            self._events.publish(DomainEvent(
                type="task.started",
                producer=self._agent_id,
                payload={"task_id": task_id},
            ))

            # ------------------------------------------------------------
            # 5. Start lease-refresh background thread
            #    FIX #2.6: keeps the lease alive for tasks that run longer
            #    than the initial lease_seconds (300 s by default).
            # ------------------------------------------------------------
            if lease_token:
                lease_refresher = _LeaseRefresher(
                    lease_port=self._lease,
                    lease_token=lease_token,
                    interval_seconds=_LEASE_REFRESH_INTERVAL,
                    extension_seconds=_LEASE_REFRESH_EXTENSION,
                )
                lease_refresher.start()

            # ------------------------------------------------------------
            # 6. Start agent session
            # ------------------------------------------------------------
            env = self._build_env(task, ws_path, agent_props)
            session = runtime.start_session(agent_props, ws_path, env)

            # ------------------------------------------------------------
            # 7. Send execution context
            # ------------------------------------------------------------
            context = ExecutionContext(
                task_id=task_id,
                title=task.title,
                description=task.description,
                execution=task.execution,
                allowed_files=task.execution.files_allowed_to_modify,
                workspace_dir=ws_path,
                branch=branch,
                metadata={"project_id": project_id},
            )
            runtime.send_execution_payload(session, context)

            # ------------------------------------------------------------
            # 8. Wait for completion
            # ------------------------------------------------------------
            log_dir = LOG_BASE / task_id
            log_dir.mkdir(parents=True, exist_ok=True)

            result = runtime.wait_for_completion(session, self._timeout)
            self._save_logs(log_dir, result)   # FIX #3.3: errors are warnings, not crashes

            # ------------------------------------------------------------
            # 9. Validate modified files
            # ------------------------------------------------------------
            actual_modified = self._git.get_modified_files(ws_path)
            violations = self._check_allowed_files(
                actual_modified, task.execution.files_allowed_to_modify
            )
            if violations:
                raise _ForbiddenFileEdit(violations)

            # ------------------------------------------------------------
            # 10. Check agent exit code
            # ------------------------------------------------------------
            if not result.success:
                raise _AgentFailed(
                    f"Agent exited with code {result.exit_code}\n{result.stderr}"
                )

            # ------------------------------------------------------------
            # 11. Run acceptance tests  (FIX #8: shell=False)
            # ------------------------------------------------------------
            if task.execution.test_command:
                self._run_tests(ws_path, task.execution.test_command)

            # ------------------------------------------------------------
            # 12. Commit + push
            # ------------------------------------------------------------
            commit_sha = self._git.apply_changes_and_commit(
                ws_path, f"task({task_id}): {task.title}"
            )
            self._git.push_branch(ws_path, branch)

            # ------------------------------------------------------------
            # 13. Persist success
            # ------------------------------------------------------------
            task_result = TaskResult(
                branch=branch,
                commit_sha=commit_sha,
                modified_files=actual_modified,
                artifacts=result.artifacts,
            )
            expected_v = task.state_version
            task.complete(task_result)
            self._task_repo.update_if_version(task_id, task, expected_v)

            self._events.publish(DomainEvent(
                type="task.completed",
                producer=self._agent_id,
                payload={"task_id": task_id, "commit_sha": commit_sha},
            ))
            log.info("worker.task_succeeded", task_id=task_id, commit_sha=commit_sha)

        except _ForbiddenFileEdit as exc:
            self._handle_failure(task, f"Forbidden file edits: {exc.violations}")
        except _AgentFailed as exc:
            self._handle_failure(task, str(exc))
        except _TestsFailed as exc:
            self._handle_failure(task, str(exc))
        except Exception as exc:
            log.exception("worker.unexpected_error", task_id=task_id, error=str(exc))
            self._handle_failure(task, f"Unexpected error: {exc}")
        finally:
            # Stop lease refresher before revoking so it doesn't race
            if lease_refresher:
                lease_refresher.stop()
            if session:
                try:
                    runtime.terminate_session(session)
                except Exception:
                    pass
            if ws_path:
                self._git.cleanup_workspace(ws_path)
            # Use the token captured at entry; task may have been reloaded
            if lease_token:
                self._lease.revoke_lease(lease_token)

    # ------------------------------------------------------------------
    # FIX #1.1 — CAS retry loop for task.start()
    # ------------------------------------------------------------------

    def _start_task_with_retry(self, task_id: str) -> TaskAggregate:
        """
        Transition the task to IN_PROGRESS with optimistic-concurrency retry.

        The reconciler may write to the same YAML between our initial load()
        and update_if_version(). A single version conflict should not waste a
        full task retry. We reload fresh state and retry MAX_UPDATE_RETRIES times.
        """
        for attempt in range(MAX_UPDATE_RETRIES):
            fresh = self._task_repo.load(task_id)

            if fresh.status != TaskStatus.ASSIGNED:
                raise RuntimeError(
                    f"Task {task_id} is {fresh.status.value}, expected assigned "
                    f"(CAS attempt {attempt})"
                )
            if fresh.assignment is None or fresh.assignment.agent_id != self._agent_id:
                raise RuntimeError(
                    f"Task {task_id} assignment changed under us before start "
                    f"(CAS attempt {attempt})"
                )

            expected_v = fresh.state_version
            fresh.start()
            if self._task_repo.update_if_version(task_id, fresh, expected_v):
                log.debug("worker.start_cas_ok", task_id=task_id, attempt=attempt)
                return fresh

            log.warning("worker.start_cas_conflict", task_id=task_id, attempt=attempt)

        raise RuntimeError(
            f"Version conflict starting task {task_id} after {MAX_UPDATE_RETRIES} retries"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_assignment(self, task: TaskAggregate) -> None:
        if task.assignment is None:
            raise RuntimeError(f"Task {task.task_id} has no assignment")
        if task.assignment.agent_id != self._agent_id:
            raise RuntimeError(
                f"Task {task.task_id} assigned to {task.assignment.agent_id}, "
                f"not this worker ({self._agent_id})"
            )
        if task.status != TaskStatus.ASSIGNED:
            raise RuntimeError(
                f"Task {task.task_id} is {task.status.value}, expected assigned"
            )

    def _build_env(
        self,
        task: TaskAggregate,
        ws_path: str,
        agent_props: AgentProps,
    ) -> dict[str, str]:
        """
        Build the subprocess environment for the agent session.

        FIX #1.5: runtime_config values from AgentProps are merged in,
        enabling per-agent API keys, model names, and CLI flags to be set
        declaratively in the agent registry instead of being inherited
        implicitly from the parent process environment.

        Non-string config values (bool, int, dict) are JSON-encoded.
        """
        env: dict[str, str] = {
            "TASK_ID": task.task_id,
            "WORKSPACE": ws_path,
            "TASK_TITLE": task.title,
            "AGENT_ID": agent_props.agent_id,
            "RUNTIME_TYPE": agent_props.runtime_type,
        }
        for key, value in agent_props.runtime_config.items():
            env[key] = value if isinstance(value, str) else json.dumps(value)
        return env

    @staticmethod
    def _check_allowed_files(modified: list[str], allowed: list[str]) -> list[str]:
        allowed_set = set(allowed)
        return [f for f in modified if f not in allowed_set]

    @staticmethod
    def _run_tests(ws_path: str, test_command: str) -> None:
        """
        Run the acceptance-test command.

        FIX #8: shlex.split + shell=False prevents shell-injection if
        test_command ever comes from a user-writable task YAML.

        FIX #3.2: stdout/stderr are capped at MAX_OUTPUT_BYTES so a
        test suite that floods output cannot OOM the orchestrator process.
        """
        log.info("worker.running_tests", command=test_command, cwd=ws_path)
        cmd_parts = shlex.split(test_command)
        try:
            proc = subprocess.run(
                cmd_parts,
                shell=False,
                cwd=ws_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
        except FileNotFoundError as exc:
            raise _TestsFailed(
                f"Test command not found: {cmd_parts[0]!r} — {exc}"
            ) from exc

        stdout = proc.stdout[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr = proc.stderr[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")

        if proc.returncode != 0:
            raise _TestsFailed(
                f"Tests failed (exit {proc.returncode})\n"
                f"stdout: {stdout}\nstderr: {stderr}"
            )
        log.info("worker.tests_passed", command=test_command)

    @staticmethod
    def _save_logs(log_dir: Path, result: AgentExecutionResult) -> None:
        """
        Persist agent stdout/stderr and run metadata to disk.

        FIX #3.3: OSError (full disk, permissions) is caught and logged as a
        warning. It must not propagate as a task failure — the agent may have
        actually succeeded.
        """
        try:
            (log_dir / "stdout.txt").write_text(result.stdout, encoding="utf-8")
            (log_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
            meta = {
                "exit_code": result.exit_code,
                "success": result.success,
                "elapsed_seconds": result.elapsed_seconds,
                "modified_files": result.modified_files,
            }
            (log_dir / "metadata.json").write_text(
                json.dumps(meta, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            log.warning("worker.save_logs_failed", log_dir=str(log_dir), error=str(exc))

    def _handle_failure(self, task: TaskAggregate, reason: str) -> None:
        log.error("worker.task_failed", task_id=task.task_id, reason=reason)
        try:
            if task.status not in (TaskStatus.IN_PROGRESS, TaskStatus.ASSIGNED):
                return
            expected_v = task.state_version
            task.fail(reason)
            self._task_repo.update_if_version(task.task_id, task, expected_v)
            self._events.publish(DomainEvent(
                type="task.failed",
                producer=self._agent_id,
                payload={"task_id": task.task_id, "reason": reason},
            ))
        except Exception as exc:
            log.exception("worker.failure_handler_error", error=str(exc))


# ---------------------------------------------------------------------------
# FIX #2.6 — Background lease-refresh daemon thread
# ---------------------------------------------------------------------------

class _LeaseRefresher:
    """
    Keeps a task lease alive in the background while the agent session runs.

    Spawns a daemon thread that calls lease_port.refresh_lease() every
    interval_seconds, extending the expiry by extension_seconds each time.
    The thread is a daemon so it does not prevent process shutdown.

    Call stop() before revoking the lease; otherwise the refresher may race
    with the final revoke and log spurious warnings.
    """

    def __init__(
        self,
        lease_port: LeasePort,
        lease_token: str,
        interval_seconds: int = _LEASE_REFRESH_INTERVAL,
        extension_seconds: int = _LEASE_REFRESH_EXTENSION,
    ) -> None:
        self._lease = lease_port
        self._token = lease_token
        self._interval = interval_seconds
        self._extension = extension_seconds
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"lease-refresher-{lease_token[:8]}",
        )

    def start(self) -> None:
        self._thread.start()
        log.debug("lease_refresher.started", token=self._token[:8])

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=5)
        log.debug("lease_refresher.stopped", token=self._token[:8])

    def _run(self) -> None:
        while not self._stop_event.wait(timeout=self._interval):
            ok = self._lease.refresh_lease(self._token, self._extension)
            if ok:
                log.debug("lease_refresher.refreshed", token=self._token[:8])
            else:
                log.warning(
                    "lease_refresher.refresh_failed",
                    token=self._token[:8],
                    reason="lease may have been revoked by reconciler",
                )


# ---------------------------------------------------------------------------
# Internal exception types
# ---------------------------------------------------------------------------

class _ForbiddenFileEdit(Exception):
    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__(str(violations))


class _AgentFailed(Exception):
    pass


class _TestsFailed(Exception):
    pass