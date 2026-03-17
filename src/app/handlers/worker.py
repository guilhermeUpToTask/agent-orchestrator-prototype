"""
src/app/handlers/worker.py — Worker event handler (thin router).

Phase 2 refactoring: all execution pipeline logic has been moved to:
    src/app/services/worker_execution.py  (WorkerExecutionService)
    src/infra/redis_adapters/lease_refresher.py  (LeaseRefresher)

This handler now has one responsibility: receive a task.assigned event and
delegate processing to WorkerExecutionService.

Handler contract:
    handle(event) → service.execute(event)

Constructor accepts the same arguments as before so existing tests and the
factory do not need updating. The handler internally constructs the service.
_LeaseRefresher is re-exported here for backward compatibility with tests
that patch "src.app.handlers.worker._LeaseRefresher".
"""
from __future__ import annotations

from typing import Callable

import structlog

from src.domain import AgentProps
from src.domain import (
    AgentRegistryPort, AgentRuntimePort, EventPort,
    GitWorkspacePort, LeasePort, TaskLogsPort,
    TaskRepositoryPort, TestRunnerPort,
)
from src.app.services.worker_execution import WorkerExecutionService
from src.infra.redis_adapters.lease_refresher import LeaseRefresher

log = structlog.get_logger(__name__)

# Re-export for backward compat (tests patch this path)
_LeaseRefresher = LeaseRefresher


class WorkerHandler:
    """
    Thin event router for worker agents.

    Receives task.assigned events from the CLI event loop and delegates
    the full execution pipeline to WorkerExecutionService.

    Constructor signature is intentionally preserved from v1 so the factory
    and existing unit tests require no changes. The handler constructs the
    service internally from the provided ports.
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
        logs_port: TaskLogsPort,
        test_runner: TestRunnerPort,
        task_timeout_seconds: int = 600,
    ) -> None:
        self._agent_id = agent_id
        self._service = WorkerExecutionService(
            repo_url=repo_url,
            task_repo=task_repo,
            agent_registry=agent_registry,
            event_port=event_port,
            lease_port=lease_port,
            git_workspace=git_workspace,
            runtime_factory=runtime_factory,
            logs_port=logs_port,
            test_runner=test_runner,
            task_timeout_seconds=task_timeout_seconds,
        )

    # ------------------------------------------------------------------
    # Entry point — called by the CLI worker event loop
    # ------------------------------------------------------------------

    def process(self, task_id: str, project_id: str) -> None:
        """Delegate task execution to WorkerExecutionService."""
        self._service.execute(
            task_id=task_id,
            project_id=project_id,
            agent_id=self._agent_id,
        )
