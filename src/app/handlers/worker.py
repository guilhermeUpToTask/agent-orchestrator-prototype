"""
src/app/handlers/worker.py — Worker use-case handler.

Per-task execution flow:
  1. Load task, validate assignment
  2. Load agent props → build the correct runtime for this agent
  3. Create ephemeral git workspace
  4. Transition task → in_progress
  5. Start agent session (AgentRuntimePort)
  6. Send ExecutionContext
  7. Wait for result
  8. Validate modified files against allowed list
  9. Run acceptance tests
  10. Commit + push on success / fail on error
  11. Persist final state, emit event, cleanup
"""
from __future__ import annotations

import os
import subprocess
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

        try:
            # ------------------------------------------------------------
            # 3. Create ephemeral workspace
            # ------------------------------------------------------------
            ws_path = self._git.create_workspace(self._repo_url, task_id)
            branch = f"task/{task_id}"
            self._git.checkout_main_and_create_branch(ws_path, branch)
            log.info("worker.workspace_ready", task_id=task_id, path=ws_path)

            # ------------------------------------------------------------
            # 4. Transition task → in_progress (persist-first)
            # ------------------------------------------------------------
            expected_v = task.state_version
            task.start()
            ok = self._task_repo.update_if_version(task_id, task, expected_v)
            if not ok:
                raise RuntimeError(f"Version conflict starting task {task_id}")

            self._events.publish(DomainEvent(
                type="task.started",
                producer=self._agent_id,
                payload={"task_id": task_id},
            ))

            # ------------------------------------------------------------
            # 5. Start agent session
            # ------------------------------------------------------------
            env = self._build_env(task, ws_path)
            session = runtime.start_session(agent_props, ws_path, env)

            # ------------------------------------------------------------
            # 6. Send execution context
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
            # 7. Wait for completion
            # ------------------------------------------------------------
            log_dir = LOG_BASE / task_id
            log_dir.mkdir(parents=True, exist_ok=True)

            result = runtime.wait_for_completion(session, self._timeout)
            self._save_logs(log_dir, result)

            # ------------------------------------------------------------
            # 8. Validate modified files
            # ------------------------------------------------------------
            actual_modified = self._git.get_modified_files(ws_path)
            violations = self._check_allowed_files(
                actual_modified, task.execution.files_allowed_to_modify
            )
            if violations:
                raise _ForbiddenFileEdit(violations)

            # ------------------------------------------------------------
            # 9. Check agent exit code
            # ------------------------------------------------------------
            if not result.success:
                raise _AgentFailed(
                    f"Agent exited with code {result.exit_code}\n{result.stderr}"
                )

            # ------------------------------------------------------------
            # 10. Run acceptance tests
            # ------------------------------------------------------------
            if task.execution.test_command:
                self._run_tests(ws_path, task.execution.test_command)

            # ------------------------------------------------------------
            # 11. Commit + push
            # ------------------------------------------------------------
            commit_sha = self._git.apply_changes_and_commit(
                ws_path, f"task({task_id}): {task.title}"
            )
            self._git.push_branch(ws_path, branch)

            # ------------------------------------------------------------
            # 12. Persist success
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
            if session:
                try:
                    runtime.terminate_session(session)
                except Exception:
                    pass
            if ws_path:
                self._git.cleanup_workspace(ws_path)
            if task.assignment and task.assignment.lease_token:
                self._lease.revoke_lease(task.assignment.lease_token)

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

    def _build_env(self, task: TaskAggregate, ws_path: str) -> dict[str, str]:
        return {
            "TASK_ID": task.task_id,
            "WORKSPACE": ws_path,
            "TASK_TITLE": task.title,
        }

    @staticmethod
    def _check_allowed_files(modified: list[str], allowed: list[str]) -> list[str]:
        allowed_set = set(allowed)
        return [f for f in modified if f not in allowed_set]

    @staticmethod
    def _run_tests(ws_path: str, test_command: str) -> None:
        log.info("worker.running_tests", command=test_command, cwd=ws_path)
        proc = subprocess.run(
            test_command,
            shell=True,
            cwd=ws_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            raise _TestsFailed(
                f"Tests failed (exit {proc.returncode})\n"
                f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
            )
        log.info("worker.tests_passed", command=test_command)

    @staticmethod
    def _save_logs(log_dir: Path, result: AgentExecutionResult) -> None:
        (log_dir / "stdout.txt").write_text(result.stdout)
        (log_dir / "stderr.txt").write_text(result.stderr)
        import json
        meta = {
            "exit_code": result.exit_code,
            "success": result.success,
            "elapsed_seconds": result.elapsed_seconds,
            "modified_files": result.modified_files,
        }
        (log_dir / "metadata.json").write_text(json.dumps(meta, indent=2))

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