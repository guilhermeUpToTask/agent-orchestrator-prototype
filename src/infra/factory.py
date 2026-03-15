"""
src/infra/factory.py — Dependency injection factory.

Reads AGENT_MODE env var:
  dry-run  → in-memory + dry-run adapters (CI/tests)
  real     → Redis + per-agent runtime adapters (production)

Each agent in the registry declares its own runtime_type ("gemini", "claude", etc.)
so multiple agents with different LLM backends can coexist in the same system.
"""
from __future__ import annotations

import os
from typing import Callable

import structlog

from src.app.handlers.task_manager import TaskManagerHandler
from src.app.handlers.worker import WorkerHandler
from src.app.reconciler import Reconciler
from src.core.models import AgentProps
from src.core.ports import AgentRuntimePort
from src.core.services import SchedulerService

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
    """
    Build the correct runtime adapter for the given agent based on its
    runtime_type field. Each agent CLI has its own adapter that knows
    how to invoke that specific tool, build prompts for it, and parse
    its output — keeping the orchestration layer fully agent-agnostic.

    runtime_type values:
      "dry-run" — deterministic stub (CI/tests, always available)
      "gemini"  — Gemini CLI (requires GEMINI_API_KEY)
      "claude"  — Claude Code CLI (requires ANTHROPIC_API_KEY)
    """
    if app_config.mode == "dry-run" or agent_props.runtime_type == "dry-run":
        from src.infra.runtime.dry_run_runtime import SimulatedAgentRuntime
        return SimulatedAgentRuntime()

    def _build_gemini(cfg: dict) -> AgentRuntimePort:
        from src.infra.runtime.gemini_runtime import GeminiAgentRuntime
        return GeminiAgentRuntime(
            api_key=os.getenv("GEMINI_API_KEY", ""),
            model=cfg.get("model", GeminiAgentRuntime.DEFAULT_MODEL),
            extra_flags=cfg.get("extra_flags", []),
        )

    def _build_claude(cfg: dict) -> AgentRuntimePort:
        from src.infra.runtime.claude_code_runtime import ClaudeCodeRuntime
        return ClaudeCodeRuntime(
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            model=cfg.get("model", ClaudeCodeRuntime.DEFAULT_MODEL),
            extra_flags=cfg.get("extra_flags", []),
        )

    registry: dict[str, Callable[[dict], AgentRuntimePort]] = {
        "gemini": _build_gemini,
        "claude": _build_claude,
    }

    builder = registry.get(agent_props.runtime_type)
    if not builder:
        raise ValueError(
            f"Unknown runtime_type '{agent_props.runtime_type}' for agent "
            f"'{agent_props.agent_id}'. Valid values: {', '.join(list(registry.keys()) + ['dry-run'])}"
        )

    return builder(agent_props.runtime_config)


def build_runtime_factory() -> Callable[[AgentProps], AgentRuntimePort]:
    """
    Returns a callable that maps AgentProps → AgentRuntimePort.
    Passed to WorkerHandler so it can build the correct runtime per-task
    after it knows which agent was assigned.
    """
    return build_agent_runtime


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