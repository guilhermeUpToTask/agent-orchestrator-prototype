"""
src/app/services/worker_execution.py — Worker execution application service.

Extracted from src/app/handlers/worker.py as part of Phase 2 refactoring.
Previously, WorkerHandler mixed event-handling (infrastructure concern) with
the full task-execution pipeline (application concern). This service owns the
pipeline; the handler is now a thin event router that delegates here.

Execution pipeline (10 steps):
  1.  Load task, validate assignment
  2.  Load agent props → build the correct runtime for this agent
  3.  Create ephemeral git workspace
  4.  Transition task → IN_PROGRESS  (CAS with retry)
  5.  Start background lease-refresh thread
  6.  Start agent session (AgentRuntimePort)
  7.  Send ExecutionContext
  8.  Wait for result
  9.  Validate modified files against allowed list
  10. Run acceptance tests (shell=False for safety)
  11. Commit + push on success / fail on error
  12. Persist final state, emit event, cleanup
"""

from __future__ import annotations

import json
from typing import Any, Callable

import structlog

from src.core.models import (
    AgentExecutionResult,
    AgentProps,
    DomainEvent,
    ExecutionContext,
    ForbiddenFileEditError,
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
    TaskLogsPort,
    TaskRepositoryPort,
    TestRunnerPort,
)
from src.infra.redis_adapters.lease_refresher import LeaseRefresher

log = structlog.get_logger(__name__)

MAX_UPDATE_RETRIES = 5


class WorkerExecutionService:
    """
    Owns the complete task execution pipeline for a single worker agent.

    This is an application service: it orchestrates ports (infra adapters)
    and delegates domain decisions to the TaskAggregate and domain services.
    It contains no business rules of its own — only coordination logic.

    runtime_factory is a callable that receives AgentProps and returns the
    correct AgentRuntimePort for that agent's runtime_type. This makes the
    system agent-agnostic — Gemini, Claude Code, or any future CLI can be
    swapped per-agent without changing orchestration logic.
    """

    def __init__(
        self,
        repo_url: str,
        task_repo: TaskRepositoryPort,
        agent_registry: AgentRegistryPort,
        event_port: EventPort,
        lease_port: LeasePort,
        git_workspace: GitWorkspacePort,
        runtime_factory: Callable[[AgentProps], AgentRuntimePort],
        logs_port: TaskLogsPort,
        test_runner: TestRunnerPort,
        task_timeout_seconds: int = 600,
    ) -> None:
        self._repo_url = repo_url
        self._task_repo = task_repo
        self._registry = agent_registry
        self._events = event_port
        self._lease = lease_port
        self._git = git_workspace
        self._runtime_factory = runtime_factory
        self._logs = logs_port
        self._tests = test_runner
        self._timeout = task_timeout_seconds

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def execute(self, task_id: str, project_id: str, agent_id: str) -> None:
        """
        Run the full execution pipeline for task_id assigned to agent_id.
        All side effects (git, agent, persistence) go through ports.
        """
        log.info("worker.process_start", task_id=task_id, agent_id=agent_id)

        task = self._task_repo.load(task_id)
        self._validate_assignment(task, agent_id)

        lease_token: str | None = task.assignment.lease_token if task.assignment else None

        agent_props = self._registry.get(agent_id)
        if agent_props is None:
            raise RuntimeError(f"Agent {agent_id} not found in registry")

        runtime = self._runtime_factory(agent_props)
        log.info(
            "worker.runtime_selected",
            task_id=task_id,
            agent_id=agent_id,
            runtime_type=agent_props.runtime_type,
        )

        ws_path: str | None = None
        session = None
        lease_refresher: LeaseRefresher | None = None

        try:
            ws_path, branch = self._prepare_workspace(task_id)

            task = self._start_task_with_retry(task_id, agent_id)
            self._events.publish(
                DomainEvent(
                    type="task.started",
                    producer=agent_id,
                    payload={"task_id": task_id},
                )
            )

            if lease_token:
                lease_refresher = LeaseRefresher(
                    lease_port=self._lease,
                    lease_token=lease_token,
                )
                lease_refresher.start()

            session, result = self._run_agent_session(
                runtime, agent_props, task, ws_path, branch, project_id
            )

            commit_sha, actual_modified = self._validate_and_commit(task, ws_path, branch, result)

            self._persist_success(
                task, agent_id, branch, commit_sha, actual_modified, result.artifacts
            )

        except ForbiddenFileEditError as exc:
            self._handle_failure(task, agent_id, f"Forbidden file edits: {exc.violations}")
        except _AgentFailed as exc:
            self._handle_failure(task, agent_id, str(exc))
        except _TestsFailed as exc:
            self._handle_failure(task, agent_id, str(exc))
        except Exception as exc:
            log.exception("worker.unexpected_error", task_id=task_id, error=str(exc))
            self._handle_failure(task, agent_id, f"Unexpected error: {exc}")
        finally:
            if lease_refresher:
                lease_refresher.stop()
            if session:
                try:
                    runtime.terminate_session(session)
                except Exception:
                    pass
            if ws_path:
                self._git.cleanup_workspace(ws_path)
            if lease_token:
                self._lease.revoke_lease(lease_token)

    # ------------------------------------------------------------------
    # CAS retry loop for task.start()
    # ------------------------------------------------------------------

    def _start_task_with_retry(self, task_id: str, agent_id: str) -> TaskAggregate:
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
            if fresh.assignment is None or fresh.assignment.agent_id != agent_id:
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

    @staticmethod
    def _validate_assignment(task: TaskAggregate, agent_id: str) -> None:
        if task.assignment is None:
            raise RuntimeError(f"Task {task.task_id} has no assignment")
        if task.assignment.agent_id != agent_id:
            raise RuntimeError(
                f"Task {task.task_id} assigned to {task.assignment.agent_id}, "
                f"not this worker ({agent_id})"
            )
        if task.status != TaskStatus.ASSIGNED:
            raise RuntimeError(f"Task {task.task_id} is {task.status.value}, expected assigned")

    def _build_env(
        self,
        task: TaskAggregate,
        ws_path: str,
        agent_props: AgentProps,
    ) -> dict[str, str]:
        """
        Build the subprocess environment for the agent session.

        runtime_config values from AgentProps are merged in, enabling per-agent
        API keys, model names, and CLI flags to be set declaratively in the
        agent registry instead of being inherited from the parent process env.

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

    def _handle_failure(self, task: TaskAggregate, agent_id: str, reason: str) -> None:
        log.error("worker.task_failed", task_id=task.task_id, reason=reason)
        try:
            if task.status not in (TaskStatus.IN_PROGRESS, TaskStatus.ASSIGNED):
                return
            expected_v = task.state_version
            task.fail(reason)
            self._task_repo.update_if_version(task.task_id, task, expected_v)
            self._events.publish(
                DomainEvent(
                    type="task.failed",
                    producer=agent_id,
                    payload={"task_id": task.task_id, "reason": reason},
                )
            )
        except Exception as exc:
            log.exception("worker.failure_handler_error", error=str(exc))

    def _prepare_workspace(self, task_id: str) -> tuple[str, str]:
        log.info("worker.preparing_workspace", task_id=task_id, repo_url=self._repo_url)
        ws_path = self._git.create_workspace(self._repo_url, task_id)
        branch = f"task/{task_id}"
        self._git.checkout_main_and_create_branch(ws_path, branch)
        log.info("worker.workspace_ready", task_id=task_id, path=ws_path, branch=branch)
        return ws_path, branch

    def _run_agent_session(
        self,
        runtime: AgentRuntimePort,
        agent_props: AgentProps,
        task: TaskAggregate,
        ws_path: str,
        branch: str,
        project_id: str,
    ) -> tuple[Any, AgentExecutionResult]:
        log.info("worker.agent_session_starting", task_id=task.task_id, workspace=ws_path)
        env = self._build_env(task, ws_path, agent_props)
        session = runtime.start_session(agent_props, ws_path, env)

        context = ExecutionContext(
            task_id=task.task_id,
            title=task.title,
            description=task.description,
            execution=task.execution,
            allowed_files=task.execution.files_allowed_to_modify,
            workspace_dir=ws_path,
            branch=branch,
            metadata={"project_id": project_id},
        )
        runtime.send_execution_payload(session, context)
        log.info("worker.agent_session_payload_sent", task_id=task.task_id)

        log.info(
            "worker.agent_session_waiting",
            task_id=task.task_id,
            timeout=self._timeout,
        )
        result: AgentExecutionResult = runtime.wait_for_completion(session, self._timeout)
        log.info(
            "worker.agent_session_completed",
            task_id=task.task_id,
            success=result.success,
            exit_code=result.exit_code,
            elapsed_seconds=result.elapsed_seconds,
        )
        self._logs.save_logs(task.task_id, result)
        return session, result

    def _validate_and_commit(
        self,
        task: TaskAggregate,
        ws_path: str,
        branch: str,
        result: AgentExecutionResult,
    ) -> tuple[str, list[str]]:
        actual_modified = self._git.get_modified_files(ws_path)
        log.info(
            "worker.validating_modifications",
            task_id=task.task_id,
            modified_count=len(actual_modified),
            modified_files=actual_modified,
        )
        # Domain validates policy: ExecutionSpec.validate_modifications raises
        # ForbiddenFileEditError if any file is outside the allowed set.
        task.execution.validate_modifications(actual_modified)

        if not result.success:
            raise _AgentFailed(f"Agent exited with code {result.exit_code}\n{result.stderr}")

        if task.execution.test_command:
            log.info(
                "worker.running_acceptance_tests",
                task_id=task.task_id,
                cmd=task.execution.test_command,
            )
            self._tests.run_tests(ws_path, task.execution.test_command)

        log.info("worker.committing_changes", task_id=task.task_id, branch=branch)
        commit_sha = self._git.apply_changes_and_commit(
            ws_path, f"task({task.task_id}): {task.title}"
        )
        log.info("worker.pushing_changes", task_id=task.task_id, sha=commit_sha)
        self._git.push_branch(ws_path, branch)
        return commit_sha, actual_modified

    def _persist_success(
        self,
        task: TaskAggregate,
        agent_id: str,
        branch: str,
        commit_sha: str,
        actual_modified: list[str],
        artifacts: dict,
    ) -> None:
        task_result = TaskResult(
            branch=branch,
            commit_sha=commit_sha,
            modified_files=actual_modified,
            artifacts=artifacts,
        )
        expected_v = task.state_version
        task.complete(task_result)
        self._task_repo.update_if_version(task.task_id, task, expected_v)

        self._events.publish(
            DomainEvent(
                type="task.completed",
                producer=agent_id,
                payload={"task_id": task.task_id, "commit_sha": commit_sha},
            )
        )
        log.info("worker.task_succeeded", task_id=task.task_id, commit_sha=commit_sha)


# ---------------------------------------------------------------------------
# Internal exception types
# ---------------------------------------------------------------------------


class _AgentFailed(Exception):
    pass


class _TestsFailed(Exception):
    pass
