"""
src/infra/factory.py — Dependency injection factory.

Reads AGENT_MODE env var:
  dry-run  → in-memory + dry-run adapters (CI/tests)
  real     → Redis + per-agent runtime adapters (production)

Each agent in the registry declares its own runtime_type ("gemini", "claude", etc.)
so multiple agents with different LLM backends can coexist in the same system.
"""

from __future__ import annotations

from typing import Callable

import structlog

from src.app.handlers.task_manager import TaskManagerHandler
from src.app.handlers.worker import WorkerHandler
from src.app.reconciliation import Reconciler
from src.domain import AgentProps
from src.domain import AgentRuntimePort
from src.domain import SchedulerService

log = structlog.get_logger(__name__)

from src.infra.config import config as app_config


def _build_real_redis():
    import redis

    return redis.from_url(app_config.redis_url, decode_responses=False)


def build_task_repo():
    from src.infra.fs.task_repository import YamlTaskRepository

    return YamlTaskRepository(app_config.tasks_dir)


def build_agent_registry():
    from src.infra.fs.agent_registry import JsonAgentRegistry

    return JsonAgentRegistry(app_config.registry_path)


def build_event_port():
    if app_config.mode == "dry-run":
        from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter

        return InMemoryEventAdapter()
    from src.infra.redis_adapters.event_adapter import RedisEventAdapter

    return RedisEventAdapter(_build_real_redis())


def build_lease_port():
    if app_config.mode == "dry-run":
        from src.infra.redis_adapters.lease_memory import InMemoryLeaseAdapter

        return InMemoryLeaseAdapter()
    from src.infra.redis_adapters.lease_adapter import RedisLeaseAdapter

    return RedisLeaseAdapter(_build_real_redis())


def build_git_workspace():
    if app_config.mode == "dry-run":
        from src.infra.git.workspace_adapter import DryRunGitWorkspaceAdapter

        return DryRunGitWorkspaceAdapter()
    from src.infra.git.workspace_adapter import GitWorkspaceAdapter

    return GitWorkspaceAdapter(workspace_base=app_config.workspace_dir)


def build_agent_runtime(agent_props: AgentProps) -> AgentRuntimePort:
    """Delegates to src/infra/runtime/factory.py (Phase 8 refactoring)."""
    from src.infra.runtime.factory import build_agent_runtime as _build
    return _build(agent_props)


def build_runtime_factory() -> Callable[[AgentProps], AgentRuntimePort]:
    """Returns a callable that maps AgentProps → AgentRuntimePort."""
    from src.infra.runtime.factory import build_runtime_factory as _build
    return _build()


def build_task_creation_service():
    from src.app.services.task_creation import TaskCreationService

    return TaskCreationService(
        task_repo=build_task_repo(),
        event_port=build_event_port(),
    )


def build_task_manager_handler() -> TaskManagerHandler:
    return TaskManagerHandler(
        task_repo=build_task_repo(),
        agent_registry=build_agent_registry(),
        event_port=build_event_port(),
        lease_port=build_lease_port(),
        scheduler=SchedulerService(),
    )


def build_worker_handler() -> WorkerHandler:
    from src.infra.logs_and_tests import (
        FilesystemTaskLogsAdapter,
        SubprocessTestRunnerAdapter,
    )

    return WorkerHandler(
        agent_id=app_config.agent_id,
        repo_url=app_config.repo_url,
        task_repo=build_task_repo(),
        agent_registry=build_agent_registry(),
        event_port=build_event_port(),
        lease_port=build_lease_port(),
        git_workspace=build_git_workspace(),
        runtime_factory=build_runtime_factory(),
        logs_port=FilesystemTaskLogsAdapter(),
        test_runner=SubprocessTestRunnerAdapter(),
        task_timeout_seconds=app_config.task_timeout,
    )


def build_reconciler(
    interval_seconds: int = 60,
    stuck_task_min_age_seconds: int = 120,
) -> Reconciler:
    return Reconciler(
        task_repo=build_task_repo(),
        lease_port=build_lease_port(),
        event_port=build_event_port(),
        agent_registry=build_agent_registry(),
        interval_seconds=interval_seconds,
        stuck_task_min_age_seconds=stuck_task_min_age_seconds,
    )

def build_task_retry_usecase():
    from src.app.usecases.task_retry import TaskRetryUseCase

    return TaskRetryUseCase(
        task_repo=build_task_repo(),
        event_port=build_event_port(),
    )

def build_task_delete_usecase():
    from src.app.usecases.task_delete import TaskDeleteUseCase
    return TaskDeleteUseCase(task_repo=build_task_repo())


def build_task_prune_usecase():
    from src.app.usecases.task_prune import TaskPruneUseCase
    return TaskPruneUseCase(task_repo=build_task_repo())


def build_agent_register_usecase():
    from src.app.usecases.agent_register import AgentRegisterUseCase
    return AgentRegisterUseCase(agent_registry=build_agent_registry())


def build_project_reset_usecase():
    from src.app.usecases.project_reset import ProjectResetUseCase
    return ProjectResetUseCase(
        task_repo=build_task_repo(),
        lease_port=build_lease_port(),
        agent_registry=build_agent_registry(),
        repo_url=app_config.repo_url,
    )

def build_task_assign_usecase():
    from src.app.usecases.task_assign import TaskAssignUseCase
    return TaskAssignUseCase(
        task_repo=build_task_repo(),
        agent_registry=build_agent_registry(),
        event_port=build_event_port(),
        lease_port=build_lease_port(),
        scheduler=SchedulerService(),
    )


def build_task_fail_handling_usecase():
    from src.app.usecases.task_fail_handling import TaskFailHandlingUseCase
    return TaskFailHandlingUseCase(
        task_repo=build_task_repo(),
        event_port=build_event_port(),
    )


def build_task_unblock_usecase():
    from src.app.usecases.task_unblock import TaskUnblockUseCase
    return TaskUnblockUseCase(
        task_repo=build_task_repo(),
        assign_usecase=build_task_assign_usecase(),
    )

def build_task_execute_usecase():
    from src.app.usecases.task_execute import TaskExecuteUseCase
    from src.infra.logs_and_tests import (
        FilesystemTaskLogsAdapter,
        SubprocessTestRunnerAdapter,
    )
    return TaskExecuteUseCase(
        repo_url=app_config.repo_url,
        task_repo=build_task_repo(),
        agent_registry=build_agent_registry(),
        event_port=build_event_port(),
        lease_port=build_lease_port(),
        git_workspace=build_git_workspace(),
        runtime_factory=build_runtime_factory(),
        logs_port=FilesystemTaskLogsAdapter(),
        test_runner=SubprocessTestRunnerAdapter(),
        task_timeout_seconds=app_config.task_timeout,
    )
