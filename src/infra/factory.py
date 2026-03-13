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

_MODE = os.getenv("AGENT_MODE", "dry-run")
_AGENT_ID = os.getenv("AGENT_ID", "agent-worker-001")
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_TASK_TIMEOUT = int(os.getenv("TASK_TIMEOUT_SECONDS", "600"))

# All workflow state lives under ORCHESTRATOR_HOME — outside the project repo
# so bare repos and task state don't pollute the project's own git history.
# Default: ~/.orchestrator  Override: ORCHESTRATOR_HOME=/some/other/path
_HOME = os.path.abspath(os.getenv("ORCHESTRATOR_HOME", os.path.expanduser("~/.orchestrator")))

_TASKS_DIR     = os.getenv("TASKS_DIR",      os.path.join(_HOME, "tasks"))
_REGISTRY_PATH = os.getenv("REGISTRY_PATH",  os.path.join(_HOME, "agents", "registry.json"))
_REPO_URL      = os.getenv("REPO_URL",        f"file://{os.path.join(_HOME, 'repos', 'my-repo')}")
_WORKSPACE_DIR = os.getenv("WORKSPACE_DIR",   os.path.join(_HOME, "repos", "workspaces"))


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
    return GitWorkspaceAdapter(workspace_base=_WORKSPACE_DIR)


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
    if _MODE == "dry-run" or agent_props.runtime_type == "dry-run":
        from src.infra.runtime.agent_runtime import DryRunAgentRuntime
        return DryRunAgentRuntime()

    cfg = agent_props.runtime_config  # runtime-specific overrides from registry

    if agent_props.runtime_type == "gemini":
        from src.infra.runtime.gemini_runtime import GeminiAgentRuntime
        return GeminiAgentRuntime(
            api_key=os.getenv("GEMINI_API_KEY", ""),
            model=cfg.get("model", GeminiAgentRuntime.DEFAULT_MODEL),
            extra_flags=cfg.get("extra_flags", []),
        )

    if agent_props.runtime_type == "claude":
        from src.infra.runtime.claude_code_runtime import ClaudeCodeRuntime
        return ClaudeCodeRuntime(
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            model=cfg.get("model", ClaudeCodeRuntime.DEFAULT_MODEL),
            extra_flags=cfg.get("extra_flags", []),
        )

    raise ValueError(
        f"Unknown runtime_type '{agent_props.runtime_type}' for agent "
        f"'{agent_props.agent_id}'. Valid values: gemini, claude, dry-run"
    )


def build_runtime_factory() -> Callable[[AgentProps], AgentRuntimePort]:
    """
    Returns a callable that maps AgentProps → AgentRuntimePort.
    Passed to WorkerHandler so it can build the correct runtime per-task
    after it knows which agent was assigned.
    """
    return build_agent_runtime


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
        runtime_factory=build_runtime_factory(),
        task_timeout_seconds=_TASK_TIMEOUT,
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