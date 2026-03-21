"""
src/app/handlers/worker.py — Worker event handler (thin router).

Phase 2 refactoring: all execution pipeline logic has been moved to:
    src/app/usecases/task_execute.py  (TaskExecuteUseCase)
    src/infra/redis_adapters/lease_refresher.py  (LeaseRefresher)

This handler now has one responsibility: receive a task.assigned event and
delegate processing to TaskExecuteUseCase.

Handler contract:
    handle(event) → service.execute(event)
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
from src.domain.ports.lease import LeaseRefresherFactory
from src.app.usecases.task_execute import TaskExecuteUseCase

log = structlog.get_logger(__name__)


class WorkerHandler:
    """
    Thin event router for worker agents.

    Receives task.assigned events from the CLI event loop and delegates
    the full execution pipeline to TaskExecuteUseCase.
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
        lease_refresher_factory: LeaseRefresherFactory,
        task_timeout_seconds: int = 600,
    ) -> None:
        self._agent_id = agent_id
        self._service = TaskExecuteUseCase(
            repo_url=repo_url,
            task_repo=task_repo,
            agent_registry=agent_registry,
            event_port=event_port,
            lease_port=lease_port,
            git_workspace=git_workspace,
            runtime_factory=runtime_factory,
            logs_port=logs_port,
            test_runner=test_runner,
            lease_refresher_factory=lease_refresher_factory,
            task_timeout_seconds=task_timeout_seconds,
        )

    # ------------------------------------------------------------------
    # Entry point — called by the CLI worker event loop
    # ------------------------------------------------------------------

    def process(self, task_id: str, project_id: str) -> None:
        """Delegate task execution to TaskExecuteUseCase."""
        self._service.execute(
            task_id=task_id,
            project_id=project_id,
            agent_id=self._agent_id,
        )
