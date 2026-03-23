"""
src/infra/factory.py — Dependency injection factory.

Reads AGENT_MODE env var:
  dry-run  → in-memory + dry-run adapters (CI/tests)
  real     → Redis + per-agent runtime adapters (production)

Each agent in the registry declares its own runtime_type ("gemini", "claude", etc.)
so multiple agents with different LLM backends can coexist in the same system.
"""

from __future__ import annotations

from functools import lru_cache
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
from src.infra.project_paths import ProjectPaths
from src.infra.project_settings import ProjectSettingsManager


def build_project_paths() -> ProjectPaths:
    """
    Build ProjectPaths for the active project.

    This is the canonical way to get project-scoped paths.  All adapters
    and use cases should receive paths via constructor injection from this
    builder — never by importing config directly.
    """
    return ProjectPaths.for_project(
        app_config.orchestrator_home,
        app_config.project_name,
    )


def build_project_settings():
    """
    Load ProjectSettings for the active project.

    Reads project.json from the project directory.  Falls back to defaults
    (empty source_repo_url) when the file does not exist yet — safe to call
    before 'orchestrate init' has completed.
    """
    return ProjectSettingsManager(build_project_paths().project_home).load()


def _build_real_redis():
    import redis

    return redis.from_url(app_config.redis_url, decode_responses=False)


def build_task_repo():
    from src.infra.fs.task_repository import YamlTaskRepository

    return YamlTaskRepository(build_project_paths().tasks_dir)


def build_agent_registry():
    from src.infra.fs.agent_registry import JsonAgentRegistry

    return JsonAgentRegistry(build_project_paths().registry_path)


def build_event_port():
    if app_config.mode == "dry-run":
        return _build_inmemory_event_port()
    from src.infra.redis_adapters.event_adapter import RedisEventAdapter

    return RedisEventAdapter(
        _build_real_redis(),
        journal_dir=str(build_project_paths().events_dir),
    )


def build_lease_port():
    if app_config.mode == "dry-run":
        return _build_inmemory_lease_port()
    from src.infra.redis_adapters.lease_adapter import RedisLeaseAdapter

    return RedisLeaseAdapter(_build_real_redis())


def build_git_workspace():
    if app_config.mode == "dry-run":
        from src.infra.git.workspace_adapter import DryRunGitWorkspaceAdapter

        return DryRunGitWorkspaceAdapter()
    from src.infra.git.workspace_adapter import GitWorkspaceAdapter

    return GitWorkspaceAdapter(
        workspace_base=build_project_paths().workspace_dir,
        source_repo_url=build_project_settings().source_repo_url,
    )


@lru_cache(maxsize=1)
def _build_inmemory_event_port():
    from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter

    return InMemoryEventAdapter()


@lru_cache(maxsize=1)
def _build_inmemory_lease_port():
    from src.infra.redis_adapters.lease_memory import InMemoryLeaseAdapter

    return InMemoryLeaseAdapter()


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
        logs_port=FilesystemTaskLogsAdapter(logs_base=build_project_paths().logs_dir),
        test_runner=SubprocessTestRunnerAdapter(),
        lease_refresher_factory=build_lease_refresher_factory(),
        task_timeout_seconds=app_config.task_timeout,
    )


# build_reconciler moved to GitHub integration section below

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

def build_lease_refresher_factory():
    """
    Return a LeaseRefresherFactory callable.

    The factory is the only place in the codebase that knows about the
    concrete LeaseRefresher class.  The app layer (TaskExecuteUseCase)
    receives this callable and calls it to create a refresher handle,
    but never imports LeaseRefresher directly.
    """
    from src.infra.redis_adapters.lease_refresher import LeaseRefresher
    return lambda lease_port, lease_token: LeaseRefresher(
        lease_port=lease_port,
        lease_token=lease_token,
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
        logs_port=FilesystemTaskLogsAdapter(logs_base=build_project_paths().logs_dir),
        test_runner=SubprocessTestRunnerAdapter(),
        lease_refresher_factory=build_lease_refresher_factory(),
        task_timeout_seconds=app_config.task_timeout,
    )


# ---------------------------------------------------------------------------
# Goal layer
# ---------------------------------------------------------------------------

def build_goal_repo():
    from src.infra.fs.goal_repository import YamlGoalRepository
    return YamlGoalRepository(build_project_paths().goals_dir)


def build_goal_init_usecase():
    from src.app.usecases.goal_init import GoalInitUseCase
    return GoalInitUseCase(
        goal_repo=build_goal_repo(),
        task_repo=build_task_repo(),
        event_port=build_event_port(),
        git_workspace=build_git_workspace(),
        task_creation=build_task_creation_service(),
        repo_url=app_config.repo_url,
    )


def build_goal_merge_task_usecase():
    from src.app.usecases.goal_merge_task import GoalMergeTaskUseCase
    return GoalMergeTaskUseCase(
        task_repo=build_task_repo(),
        goal_repo=build_goal_repo(),
        event_port=build_event_port(),
        git_workspace=build_git_workspace(),
        repo_url=app_config.repo_url,
    )


def build_goal_cancel_task_usecase():
    from src.app.usecases.goal_cancel_task import GoalCancelTaskUseCase
    return GoalCancelTaskUseCase(
        task_repo=build_task_repo(),
        goal_repo=build_goal_repo(),
        event_port=build_event_port(),
    )


# build_goal_finalize_usecase moved to GitHub integration section below


def build_task_graph_orchestrator():
    """Original orchestrator without PR integration (used in tests)."""
    from src.app.orchestrator import TaskGraphOrchestrator
    return TaskGraphOrchestrator(
        task_repo=build_task_repo(),
        goal_repo=build_goal_repo(),
        event_port=build_event_port(),
        merge_usecase=build_goal_merge_task_usecase(),
        cancel_usecase=build_goal_cancel_task_usecase(),
        spec_repo=build_spec_repo(),
        project_name=app_config.project_name,
        create_pr_usecase=None,
    )


# ---------------------------------------------------------------------------
# ProjectSpec layer
# ---------------------------------------------------------------------------

def build_spec_repo():
    """Return the FileProjectSpecRepository for the active project."""
    from src.infra.fs.project_spec_repository import FileProjectSpecRepository
    return FileProjectSpecRepository(orchestrator_home=app_config.orchestrator_home)


def build_load_project_spec():
    from src.app.usecases.load_project_spec import LoadProjectSpec
    return LoadProjectSpec(spec_repo=build_spec_repo())


def build_validate_against_spec():
    """
    Load the active spec and return a ValidateAgainstSpec instance.

    Callers that run many validations in a single process should build this
    once and reuse it — loading the spec from disk each time is wasteful.
    """
    from src.app.usecases.validate_against_spec import ValidateAgainstSpec
    spec = build_load_project_spec().execute(app_config.project_name)
    return ValidateAgainstSpec(spec)


def build_propose_spec_change():
    from src.app.usecases.propose_spec_change import ProposeSpecChange
    return ProposeSpecChange(spec_repo=build_spec_repo())


# ---------------------------------------------------------------------------
# GitHub / PR integration layer
# ---------------------------------------------------------------------------

def build_github_client():
    """
    Return a GitHubPort adapter.

    Priority order for credentials:
      1. StubGitHubClient in dry-run mode.
      2. project.json (github_token / github_owner / github_repo) — written by
         `orchestrate init` Step 6 (GitHub Setup wizard step).
      3. Environment variables GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO —
         for CI pipelines and teams that prefer env-based config.
      4. StubGitHubClient with a warning if nothing is configured.
    """
    import os
    if app_config.mode == "dry-run":
        from src.infra.github.client import StubGitHubClient
        return StubGitHubClient()

    from src.infra.github.client import GitHubClient

    settings = build_project_settings()

    # Prefer project.json values; fall back to env vars
    token = settings.github_token        or os.environ.get("GITHUB_TOKEN", "")
    owner = settings.github_owner        or os.environ.get("GITHUB_OWNER", "")
    repo  = settings.github_repo         or os.environ.get("GITHUB_REPO", "")
    base  = settings.github_base_branch

    if not all([token, owner, repo]):
        log.warning(
            "factory.github_not_configured",
            hint=(
                "Run `orchestrate init` (Step 6 — GitHub Setup) or set "
                "GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO to enable GitHub integration."
            ),
        )
        from src.infra.github.client import StubGitHubClient
        return StubGitHubClient()

    return GitHubClient(token=token, owner=owner, repo=repo)


def build_create_goal_pr_usecase(base_branch: str = "main"):
    from src.app.usecases.create_goal_pr import CreateGoalPRUseCase
    return CreateGoalPRUseCase(
        goal_repo=build_goal_repo(),
        event_port=build_event_port(),
        github=build_github_client(),
        base_branch=base_branch,
    )


def build_sync_goal_pr_usecase():
    from src.app.usecases.sync_goal_pr_status import SyncGoalPRStatusUseCase
    spec = None
    try:
        spec = build_load_project_spec().execute(app_config.project_name)
    except Exception:
        pass  # spec not configured — CI gate will be disabled
    return SyncGoalPRStatusUseCase(
        goal_repo=build_goal_repo(),
        event_port=build_event_port(),
        github=build_github_client(),
        spec=spec,
    )


def build_reconciler(
    interval_seconds: int = 60,
    stuck_task_min_age_seconds: int = 120,
) -> "Reconciler":
    return Reconciler(
        task_repo=build_task_repo(),
        lease_port=build_lease_port(),
        event_port=build_event_port(),
        agent_registry=build_agent_registry(),
        interval_seconds=interval_seconds,
        stuck_task_min_age_seconds=stuck_task_min_age_seconds,
        goal_repo=build_goal_repo(),
        sync_pr_usecase=build_sync_goal_pr_usecase(),
        advance_pr_usecase=build_advance_goal_from_pr_usecase(),
    )


def build_task_graph_orchestrator_with_pr():
    """
    Full orchestrator with GitHub PR integration enabled.
    Use build_task_graph_orchestrator() for the original no-PR version (tests).
    """
    from src.app.orchestrator import TaskGraphOrchestrator
    return TaskGraphOrchestrator(
        task_repo=build_task_repo(),
        goal_repo=build_goal_repo(),
        event_port=build_event_port(),
        merge_usecase=build_goal_merge_task_usecase(),
        cancel_usecase=build_goal_cancel_task_usecase(),
        spec_repo=build_spec_repo(),
        project_name=app_config.project_name,
        create_pr_usecase=build_create_goal_pr_usecase(),
    )


def build_goal_finalize_usecase():
    from src.app.usecases.goal_finalize import GoalFinalizeUseCase
    return GoalFinalizeUseCase(
        goal_repo=build_goal_repo(),
        event_port=build_event_port(),
    )


# ---------------------------------------------------------------------------
# Project state
# ---------------------------------------------------------------------------

def build_project_state_adapter():
    """
    Return a ProjectStatePort adapter for the active project.
    Dry-run mode uses the in-memory adapter (no disk I/O, no persistence).
    """
    if app_config.mode == "dry-run":
        from src.infra.fs.project_state_adapter import InMemoryProjectStateAdapter
        return InMemoryProjectStateAdapter()
    from src.infra.fs.project_state_adapter import FilesystemProjectStateAdapter
    return FilesystemProjectStateAdapter(
        state_dir=build_project_paths().project_state_dir,
    )


# ---------------------------------------------------------------------------
# Unblock goals
# ---------------------------------------------------------------------------

def build_unblock_goals_usecase():
    from src.app.usecases.unblock_goals import UnblockGoalsUseCase
    return UnblockGoalsUseCase(
        goal_repo=build_goal_repo(),
        event_port=build_event_port(),
    )


def build_advance_goal_from_pr_usecase():
    from src.app.usecases.advance_goal_from_pr import AdvanceGoalFromPRUseCase
    return AdvanceGoalFromPRUseCase(
        goal_repo=build_goal_repo(),
        event_port=build_event_port(),
        unblock_goals_usecase=build_unblock_goals_usecase(),
        plan_repo=build_project_plan_repo(),
    )


def build_interactive_planner_runtime(io_handler=None):
    """
    Return an InteractivePlannerRuntime for DISCOVERY mode.
    Dry-run mode uses StubInteractivePlannerRuntime.
    """
    if app_config.mode == "dry-run":
        from src.infra.runtime.interactive_planner_runtime import StubInteractivePlannerRuntime
        return StubInteractivePlannerRuntime()
    from src.infra.runtime.interactive_planner_runtime import InteractivePlannerRuntime
    return InteractivePlannerRuntime(
        api_key=app_config.anthropic_api_key.get_secret_value(),
        io_handler=io_handler,
    )


def build_planner_orchestrator(io_handler=None):
    """
    Build PlannerOrchestrator with all dependencies wired.
    """
    from src.app.usecases.planner_orchestrator import PlannerOrchestrator
    from src.app.services.decision_apply import apply_decision_to_spec
    from src.app.usecases.validate_against_spec import ValidateAgainstSpec

    spec = build_load_project_spec().execute(app_config.project_name)
    return PlannerOrchestrator(
        plan_repo=build_project_plan_repo(),
        session_repo=build_planner_session_repo(),
        context_assembler=build_planner_context_assembler(),
        autonomous_runtime=build_planner_runtime(),
        interactive_runtime=build_interactive_planner_runtime(io_handler),
        goal_init=build_goal_init_usecase(),
        validator=ValidateAgainstSpec(spec),
        project_state=build_project_state_adapter(),
        agent_registry=build_agent_registry(),
        goal_repo=build_goal_repo(),
        spec_repo=build_spec_repo(),
        project_name=app_config.project_name,
    )


# ---------------------------------------------------------------------------
# Project plan
# ---------------------------------------------------------------------------

def build_project_plan_repo():
    """
    Return a ProjectPlanRepositoryPort for the active project.
    Dry-run mode uses the in-memory adapter.
    """
    if app_config.mode == "dry-run":
        from src.infra.fs.project_plan_repository import InMemoryProjectPlanRepository
        return InMemoryProjectPlanRepository()
    from src.infra.fs.project_plan_repository import YamlProjectPlanRepository
    return YamlProjectPlanRepository(build_project_paths().plan_path)


# ---------------------------------------------------------------------------
# Planner context
# ---------------------------------------------------------------------------

def build_planner_context_assembler():
    """
    Build a PlannerContextAssembler for the active project.

    Loads the ProjectSpec eagerly — callers that need to assemble context
    many times should reuse the returned assembler rather than calling this
    builder repeatedly.
    """
    from src.app.services.planner_context import PlannerContextAssembler
    spec = build_load_project_spec().execute(app_config.project_name)
    return PlannerContextAssembler(
        spec=spec,
        project_state=build_project_state_adapter(),
        goal_repo=build_goal_repo(),
        task_repo=build_task_repo(),
        plan_repo=build_project_plan_repo(),
    )


# ---------------------------------------------------------------------------
# Planner sessions
# ---------------------------------------------------------------------------

def build_planner_session_repo():
    """
    Return a PlannerSessionRepositoryPort for the active project.
    Dry-run mode uses the in-memory adapter.
    """
    if app_config.mode == "dry-run":
        from src.infra.fs.planner_session_repository import InMemoryPlannerSessionRepository
        return InMemoryPlannerSessionRepository()
    from src.infra.fs.planner_session_repository import YamlPlannerSessionRepository
    return YamlPlannerSessionRepository(build_project_paths().planner_sessions_dir)


def build_planner_runtime():
    """
    Return a PlannerRuntimePort for the active project.
    Dry-run mode uses the deterministic StubPlannerRuntime.
    """
    if app_config.mode == "dry-run":
        from src.infra.runtime.planner_runtime import StubPlannerRuntime
        return StubPlannerRuntime()
    from src.infra.runtime.planner_runtime import AnthropicPlannerRuntime
    return AnthropicPlannerRuntime(
        api_key=app_config.anthropic_api_key.get_secret_value(),
    )


def build_run_planning_session_usecase():
    """Wire all dependencies for RunPlanningSessionUseCase."""
    from src.app.usecases.run_planning_session import RunPlanningSessionUseCase
    from src.app.usecases.validate_against_spec import ValidateAgainstSpec

    spec = build_load_project_spec().execute(app_config.project_name)
    return RunPlanningSessionUseCase(
        context_assembler=build_planner_context_assembler(),
        planner_runtime=build_planner_runtime(),
        session_repo=build_planner_session_repo(),
        goal_init=build_goal_init_usecase(),
        validator=ValidateAgainstSpec(spec),
        project_state=build_project_state_adapter(),
        agent_registry=build_agent_registry(),
        goal_repo=build_goal_repo(),
    )
