"""
src/infra/factory.py — Dependency injection factory.

Reads AGENT_MODE env var:
  dry-run  → in-memory + dry-run adapters (CI/tests)
  real     → Redis + subprocess + Git adapters (production)
"""
from __future__ import annotations

import os

import structlog

from src.app.handlers.task_manager import TaskManagerHandler
from src.app.handlers.worker import WorkerHandler
from src.app.reconciler import Reconciler
from src.core.services import SchedulerService

log = structlog.get_logger(__name__)

_MODE = os.getenv("AGENT_MODE", "dry-run")
_AGENT_ID = os.getenv("AGENT_ID", "agent-worker-001")
_REPO_URL = os.getenv("REPO_URL", "file:///tmp/test-repo")
_TASKS_DIR = os.getenv("TASKS_DIR", "workflow/tasks")
_REGISTRY_PATH = os.getenv("REGISTRY_PATH", "workflow/agents/registry.json")
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_TASK_TIMEOUT = int(os.getenv("TASK_TIMEOUT_SECONDS", "600"))


def _build_real_redis():
    import redis
    return redis.from_url(_REDIS_URL, decode_responses=False)


def build_task_repo():
    from src.infra.fs.task_repository import YamlTaskRepository
    return YamlTaskRepository(_TASKS_DIR)


def build_agent_registry():
    from src.infra.fs.agent_registry import JsonAgentRegistry
    return JsonAgentRegistry(_REGISTRY_PATH)


def build_event_port():
    if _MODE == "dry-run":
        from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter
        return InMemoryEventAdapter()
    from src.infra.redis_adapters.event_adapter import RedisEventAdapter
    return RedisEventAdapter(_build_real_redis())


def build_lease_port():
    if _MODE == "dry-run":
        from src.infra.redis_adapters.lease_memory import InMemoryLeaseAdapter
        return InMemoryLeaseAdapter()
    from src.infra.redis_adapters.lease_adapter import RedisLeaseAdapter
    return RedisLeaseAdapter(_build_real_redis())


def build_git_workspace():
    if _MODE == "dry-run":
        from src.infra.git.workspace_adapter import DryRunGitWorkspaceAdapter
        return DryRunGitWorkspaceAdapter()
    from src.infra.git.workspace_adapter import GitWorkspaceAdapter
    return GitWorkspaceAdapter()


def build_agent_runtime():
    if _MODE == "dry-run":
        from src.infra.runtime.agent_runtime import DryRunAgentRuntime
        return DryRunAgentRuntime()
    from src.infra.runtime.agent_runtime import SubprocessAgentRuntime
    binary = os.getenv("AGENT_BINARY", "/usr/local/bin/agent-cli")
    return SubprocessAgentRuntime(agent_binary=binary)


def build_task_manager_handler() -> TaskManagerHandler:
    return TaskManagerHandler(
        task_repo=build_task_repo(),
        agent_registry=build_agent_registry(),
        event_port=build_event_port(),
        lease_port=build_lease_port(),
        scheduler=SchedulerService(),
    )


def build_worker_handler() -> WorkerHandler:
    return WorkerHandler(
        agent_id=_AGENT_ID,
        repo_url=_REPO_URL,
        task_repo=build_task_repo(),
        agent_registry=build_agent_registry(),
        event_port=build_event_port(),
        lease_port=build_lease_port(),
        git_workspace=build_git_workspace(),
        agent_runtime=build_agent_runtime(),
        task_timeout_seconds=_TASK_TIMEOUT,
    )


def build_reconciler(interval_seconds: int = 30) -> Reconciler:
    return Reconciler(
        task_repo=build_task_repo(),
        lease_port=build_lease_port(),
        event_port=build_event_port(),
        interval_seconds=interval_seconds,
    )
