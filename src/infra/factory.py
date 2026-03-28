"""
src/infra/factory.py — Dependency injection factory.

Reads AGENT_MODE env var:
  dry-run  → in-memory + dry-run adapters (CI/tests)
  real     → Redis + per-agent runtime adapters (production)

Settings are loaded once at the module boundary via SettingsService and
passed explicitly to every builder.  No builder reads config files directly.

Each agent in the registry declares its own runtime_type ("gemini", "claude", etc.)
so multiple agents with different LLM backends can coexist in the same system.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Callable

import structlog

from src.infra.project_paths import ProjectPaths
from src.infra.settings import SettingsContext, SettingsService
from src.app.handlers.task_manager import TaskManagerHandler
from src.app.handlers.worker import WorkerHandler
from src.app.reconciliation import Reconciler
from src.domain import AgentProps
from src.domain import AgentRuntimePort
from src.domain import SchedulerService

log = structlog.get_logger(__name__)


def _load_context() -> SettingsContext:
    return SettingsService().load()


def build_project_paths(ctx: SettingsContext | None = None) -> ProjectPaths:
    ctx = ctx or _load_context()
    if not ctx.machine.project_name:
        raise RuntimeError("No project configured. Run `orchestrator init` first.")
    return ProjectPaths.for_project(ctx.machine.orchestrator_home, ctx.machine.project_name)


def build_project_settings(ctx: SettingsContext | None = None):
    ctx = ctx or _load_context()
    return ctx.project


def _build_real_redis(ctx: SettingsContext):
    import redis
    return redis.from_url(ctx.machine.redis_url, decode_responses=False)


def build_task_repo(ctx: SettingsContext | None = None):
    from src.infra.fs.task_repository import YamlTaskRepository
    return YamlTaskRepository(build_project_paths(ctx).tasks_dir)


def build_agent_registry(ctx: SettingsContext | None = None):
    from src.infra.fs.agent_registry import JsonAgentRegistry
    return JsonAgentRegistry(build_project_paths(ctx).registry_path)


def build_event_port(ctx: SettingsContext | None = None):
    ctx = ctx or _load_context()
    if ctx.machine.mode == "dry-run":
        return _build_inmemory_event_port()
    from src.infra.redis_adapters.event_adapter import RedisEventAdapter
    return RedisEventAdapter(_build_real_redis(ctx), journal_dir=str(build_project_paths(ctx).events_dir))


def build_telemetry_emitter(ctx: SettingsContext | None = None):
    ctx = ctx or _load_context()
    if ctx.machine.mode == "dry-run":
        return _build_inmemory_telemetry_emitter()
    from src.infra.redis_adapters.telemetry_adapter import (
        CompositeTelemetryEmitter, FileTelemetryLogEmitter,
        JsonLoggerTelemetryEmitter, RedisTelemetryEmitter,
    )
    paths = build_project_paths(ctx)
    return CompositeTelemetryEmitter([
        RedisTelemetryEmitter(_build_real_redis(ctx), journal_dir=paths.telemetry_events_dir),
        JsonLoggerTelemetryEmitter(),
        FileTelemetryLogEmitter(paths.telemetry_logs_dir / "telemetry.jsonl"),
    ])


def build_lease_port(ctx: SettingsContext | None = None):
    ctx = ctx or _load_context()
    if ctx.machine.mode == "dry-run":
        return _build_inmemory_lease_port()
    from src.infra.redis_adapters.lease_adapter import RedisLeaseAdapter
    return RedisLeaseAdapter(_build_real_redis(ctx))


def build_git_workspace(ctx: SettingsContext | None = None):
    ctx = ctx or _load_context()
    if ctx.machine.mode == "dry-run":
        from src.infra.git.workspace_adapter import DryRunGitWorkspaceAdapter
        return DryRunGitWorkspaceAdapter()
    from src.infra.git.workspace_adapter import GitWorkspaceAdapter
    return GitWorkspaceAdapter(
        workspace_base=build_project_paths(ctx).workspace_dir,
        source_repo_url=ctx.project.source_repo_url,
    )


@lru_cache(maxsize=1)
def _build_inmemory_event_port():
    from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter
    return InMemoryEventAdapter()


@lru_cache(maxsize=1)
def _build_inmemory_lease_port():
    from src.infra.redis_adapters.lease_memory import InMemoryLeaseAdapter
    return InMemoryLeaseAdapter()


@lru_cache(maxsize=1)
def _build_inmemory_telemetry_emitter():
    from src.infra.redis_adapters.telemetry_adapter import InMemoryTelemetryEmitter
    return InMemoryTelemetryEmitter()


def build_agent_runtime(agent_props: AgentProps) -> AgentRuntimePort:
    from src.infra.runtime.factory import build_agent_runtime as _build
    return _build(agent_props)


def build_runtime_factory() -> Callable[[AgentProps], AgentRuntimePort]:
    from src.infra.runtime.factory import build_runtime_factory as _build
    return _build()


def build_task_creation_service(ctx: SettingsContext | None = None):
    from src.app.services.task_creation import TaskCreationService
    return TaskCreationService(task_repo=build_task_repo(ctx), event_port=build_event_port(ctx))


def build_task_manager_handler(ctx: SettingsContext | None = None) -> TaskManagerHandler:
    return TaskManagerHandler(
        task_repo=build_task_repo(ctx), agent_registry=build_agent_registry(ctx),
        event_port=build_event_port(ctx), lease_port=build_lease_port(ctx),
        scheduler=SchedulerService(),
    )


def build_worker_handler(ctx: SettingsContext | None = None) -> WorkerHandler:
    ctx = ctx or _load_context()
    from src.infra.logs_and_tests import FilesystemTaskLogsAdapter, SubprocessTestRunnerAdapter
    paths = build_project_paths(ctx)
    return WorkerHandler(
        agent_id=ctx.machine.agent_id, repo_url=paths.repo_url,
        task_repo=build_task_repo(ctx), agent_registry=build_agent_registry(ctx),
        event_port=build_event_port(ctx), lease_port=build_lease_port(ctx),
        git_workspace=build_git_workspace(ctx), runtime_factory=build_runtime_factory(),
        logs_port=FilesystemTaskLogsAdapter(logs_base=paths.logs_dir),
        test_runner=SubprocessTestRunnerAdapter(),
        telemetry_emitter=build_telemetry_emitter(ctx),
        lease_refresher_factory=build_lease_refresher_factory(),
        task_timeout_seconds=ctx.machine.task_timeout,
    )


def build_task_retry_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.task_retry import TaskRetryUseCase
    return TaskRetryUseCase(task_repo=build_task_repo(ctx), event_port=build_event_port(ctx))


def build_task_delete_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.task_delete import TaskDeleteUseCase
    return TaskDeleteUseCase(task_repo=build_task_repo(ctx))


def build_task_prune_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.task_prune import TaskPruneUseCase
    return TaskPruneUseCase(task_repo=build_task_repo(ctx))


def build_agent_register_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.agent_register import AgentRegisterUseCase
    return AgentRegisterUseCase(agent_registry=build_agent_registry(ctx))


def build_project_reset_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.project_reset import ProjectResetUseCase
    paths = build_project_paths(ctx)
    return ProjectResetUseCase(
        task_repo=build_task_repo(ctx), lease_port=build_lease_port(ctx),
        agent_registry=build_agent_registry(ctx), repo_url=paths.repo_url,
    )


def build_task_assign_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.task_assign import TaskAssignUseCase
    return TaskAssignUseCase(
        task_repo=build_task_repo(ctx), agent_registry=build_agent_registry(ctx),
        event_port=build_event_port(ctx), lease_port=build_lease_port(ctx),
        scheduler=SchedulerService(),
    )


def build_task_fail_handling_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.task_fail_handling import TaskFailHandlingUseCase
    return TaskFailHandlingUseCase(task_repo=build_task_repo(ctx), event_port=build_event_port(ctx))


def build_task_unblock_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.task_unblock import TaskUnblockUseCase
    return TaskUnblockUseCase(task_repo=build_task_repo(ctx), assign_usecase=build_task_assign_usecase(ctx))


def build_lease_refresher_factory():
    from src.infra.redis_adapters.lease_refresher import LeaseRefresher
    return lambda lease_port, lease_token: LeaseRefresher(lease_port=lease_port, lease_token=lease_token)


def build_task_execute_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.task_execute import TaskExecuteUseCase
    from src.infra.logs_and_tests import FilesystemTaskLogsAdapter, SubprocessTestRunnerAdapter
    ctx = ctx or _load_context()
    paths = build_project_paths(ctx)
    return TaskExecuteUseCase(
        repo_url=paths.repo_url, task_repo=build_task_repo(ctx),
        agent_registry=build_agent_registry(ctx), event_port=build_event_port(ctx),
        lease_port=build_lease_port(ctx), git_workspace=build_git_workspace(ctx),
        runtime_factory=build_runtime_factory(),
        logs_port=FilesystemTaskLogsAdapter(logs_base=paths.logs_dir),
        test_runner=SubprocessTestRunnerAdapter(),
        telemetry_emitter=build_telemetry_emitter(ctx),
        lease_refresher_factory=build_lease_refresher_factory(),
        task_timeout_seconds=ctx.machine.task_timeout,
    )


def build_goal_repo(ctx: SettingsContext | None = None):
    from src.infra.fs.goal_repository import YamlGoalRepository
    return YamlGoalRepository(build_project_paths(ctx).goals_dir)


def build_goal_init_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.goal_init import GoalInitUseCase
    paths = build_project_paths(ctx)
    return GoalInitUseCase(
        goal_repo=build_goal_repo(ctx), task_repo=build_task_repo(ctx),
        event_port=build_event_port(ctx), git_workspace=build_git_workspace(ctx),
        task_creation=build_task_creation_service(ctx), repo_url=paths.repo_url,
    )


def build_goal_merge_task_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.goal_merge_task import GoalMergeTaskUseCase
    paths = build_project_paths(ctx)
    return GoalMergeTaskUseCase(
        task_repo=build_task_repo(ctx), goal_repo=build_goal_repo(ctx),
        event_port=build_event_port(ctx), git_workspace=build_git_workspace(ctx),
        repo_url=paths.repo_url, telemetry_emitter=build_telemetry_emitter(ctx),
    )


def build_goal_cancel_task_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.goal_cancel_task import GoalCancelTaskUseCase
    return GoalCancelTaskUseCase(
        task_repo=build_task_repo(ctx), goal_repo=build_goal_repo(ctx),
        event_port=build_event_port(ctx), telemetry_emitter=build_telemetry_emitter(ctx),
    )


def build_task_graph_orchestrator(ctx: SettingsContext | None = None):
    from src.app.orchestrator import TaskGraphOrchestrator
    ctx = ctx or _load_context()
    return TaskGraphOrchestrator(
        task_repo=build_task_repo(ctx), goal_repo=build_goal_repo(ctx),
        event_port=build_event_port(ctx), merge_usecase=build_goal_merge_task_usecase(ctx),
        cancel_usecase=build_goal_cancel_task_usecase(ctx), spec_repo=build_spec_repo(ctx),
        project_name=ctx.machine.project_name, create_pr_usecase=None,
        telemetry_emitter=build_telemetry_emitter(ctx),
    )


def build_spec_repo(ctx: SettingsContext | None = None):
    from src.infra.fs.project_spec_repository import FileProjectSpecRepository
    ctx = ctx or _load_context()
    return FileProjectSpecRepository(orchestrator_home=ctx.machine.orchestrator_home)


def build_load_project_spec(ctx: SettingsContext | None = None):
    from src.app.usecases.load_project_spec import LoadProjectSpec
    return LoadProjectSpec(spec_repo=build_spec_repo(ctx))


def build_validate_against_spec(ctx: SettingsContext | None = None):
    from src.app.usecases.validate_against_spec import ValidateAgainstSpec
    ctx = ctx or _load_context()
    spec = build_load_project_spec(ctx).execute(ctx.machine.project_name)
    return ValidateAgainstSpec(spec)


def build_propose_spec_change(ctx: SettingsContext | None = None):
    from src.app.usecases.propose_spec_change import ProposeSpecChange
    return ProposeSpecChange(spec_repo=build_spec_repo(ctx))


def build_github_client(ctx: SettingsContext | None = None):
    """
    Return a GitHubPort adapter.

    github_token is loaded exclusively from SecretSettings (env var GITHUB_TOKEN).
    It is never read from project.json.  Non-secret fields (owner, repo) come
    from ProjectSettings.
    """
    ctx = ctx or _load_context()
    if ctx.machine.mode == "dry-run":
        from src.infra.github.client import StubGitHubClient
        return StubGitHubClient()

    from src.infra.github.client import GitHubClient

    token = ctx.secrets.github_token
    owner = ctx.project.github_owner or ""
    repo  = ctx.project.github_repo or ""

    if not all([token, owner, repo]):
        log.warning(
            "factory.github_not_configured",
            hint=(
                "Set GITHUB_TOKEN env var and run `orchestrate init` to configure "
                "github_owner / github_repo in project.json."
            ),
        )
        from src.infra.github.client import StubGitHubClient
        return StubGitHubClient()

    return GitHubClient(token=token, owner=owner, repo=repo)


def build_create_goal_pr_usecase(base_branch: str = "main", ctx: SettingsContext | None = None):
    from src.app.usecases.create_goal_pr import CreateGoalPRUseCase
    return CreateGoalPRUseCase(
        goal_repo=build_goal_repo(ctx), event_port=build_event_port(ctx),
        github=build_github_client(ctx), base_branch=base_branch,
    )


def build_sync_goal_pr_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.sync_goal_pr_status import SyncGoalPRStatusUseCase
    ctx = ctx or _load_context()
    spec = None
    try:
        spec = build_load_project_spec(ctx).execute(ctx.machine.project_name)
    except Exception:
        pass
    return SyncGoalPRStatusUseCase(
        goal_repo=build_goal_repo(ctx), event_port=build_event_port(ctx),
        github=build_github_client(ctx), spec=spec,
    )


def build_reconciler(
    interval_seconds: int = 60,
    stuck_task_min_age_seconds: int = 120,
    ctx: SettingsContext | None = None,
) -> "Reconciler":
    return Reconciler(
        task_repo=build_task_repo(ctx), lease_port=build_lease_port(ctx),
        event_port=build_event_port(ctx), agent_registry=build_agent_registry(ctx),
        interval_seconds=interval_seconds, stuck_task_min_age_seconds=stuck_task_min_age_seconds,
        goal_repo=build_goal_repo(ctx), sync_pr_usecase=build_sync_goal_pr_usecase(ctx),
        advance_pr_usecase=build_advance_goal_from_pr_usecase(ctx),
    )


def build_task_graph_orchestrator_with_pr(ctx: SettingsContext | None = None):
    from src.app.orchestrator import TaskGraphOrchestrator
    ctx = ctx or _load_context()
    return TaskGraphOrchestrator(
        task_repo=build_task_repo(ctx), goal_repo=build_goal_repo(ctx),
        event_port=build_event_port(ctx), merge_usecase=build_goal_merge_task_usecase(ctx),
        cancel_usecase=build_goal_cancel_task_usecase(ctx), spec_repo=build_spec_repo(ctx),
        project_name=ctx.machine.project_name, create_pr_usecase=build_create_goal_pr_usecase(ctx=ctx),
        telemetry_emitter=build_telemetry_emitter(ctx),
    )


def build_goal_finalize_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.goal_finalize import GoalFinalizeUseCase
    return GoalFinalizeUseCase(goal_repo=build_goal_repo(ctx), event_port=build_event_port(ctx))


def build_project_state_adapter(ctx: SettingsContext | None = None):
    ctx = ctx or _load_context()
    if ctx.machine.mode == "dry-run":
        from src.infra.fs.project_state_adapter import InMemoryProjectStateAdapter
        base = InMemoryProjectStateAdapter()
    else:
        from src.infra.fs.project_state_adapter import FilesystemProjectStateAdapter
        base = FilesystemProjectStateAdapter(state_dir=build_project_paths(ctx).project_state_dir)
    from src.app.telemetry.project_state_wrapper import TelemetryProjectStateAdapter
    from src.app.telemetry.service import TelemetryService
    telemetry = TelemetryService(build_telemetry_emitter(ctx), producer="project-state")
    trace = telemetry.start_trace(correlation_id=ctx.machine.project_name)
    return TelemetryProjectStateAdapter(base, telemetry=telemetry, trace_context=trace)


def build_unblock_goals_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.unblock_goals import UnblockGoalsUseCase
    return UnblockGoalsUseCase(goal_repo=build_goal_repo(ctx), event_port=build_event_port(ctx))


def build_advance_goal_from_pr_usecase(ctx: SettingsContext | None = None):
    from src.app.usecases.advance_goal_from_pr import AdvanceGoalFromPRUseCase
    return AdvanceGoalFromPRUseCase(
        goal_repo=build_goal_repo(ctx), event_port=build_event_port(ctx),
        unblock_goals_usecase=build_unblock_goals_usecase(ctx),
        plan_repo=build_project_plan_repo(ctx),
    )


def build_interactive_planner_runtime(io_handler=None, ctx: SettingsContext | None = None):
    ctx = ctx or _load_context()
    if ctx.machine.mode == "dry-run":
        from src.infra.runtime.interactive_planner_runtime import StubInteractivePlannerRuntime
        return StubInteractivePlannerRuntime()
    from src.infra.runtime.interactive_planner_runtime import InteractivePlannerRuntime
    return InteractivePlannerRuntime(api_key=ctx.secrets.anthropic_api_key, io_handler=io_handler)


def build_planner_orchestrator(io_handler=None, ctx: SettingsContext | None = None):
    from src.app.usecases.planner_orchestrator import PlannerOrchestrator
    from src.app.usecases.validate_against_spec import ValidateAgainstSpec
    from src.app.telemetry.runtime_wrappers import TelemetryPlannerRuntimeWrapper
    from src.app.telemetry.service import TelemetryService
    ctx = ctx or _load_context()
    if not ctx.machine.project_name:
        raise RuntimeError("No project configured. Run `orchestrator init` first.")
    spec = build_load_project_spec(ctx).execute(ctx.machine.project_name)
    telemetry = TelemetryService(build_telemetry_emitter(ctx), producer="planner-orchestrator")
    trace = telemetry.start_trace(correlation_id=ctx.machine.project_name)
    return PlannerOrchestrator(
        plan_repo=build_project_plan_repo(ctx), session_repo=build_planner_session_repo(ctx),
        context_assembler=build_planner_context_assembler(ctx),
        autonomous_runtime=TelemetryPlannerRuntimeWrapper(build_planner_runtime(ctx), telemetry, trace),
        interactive_runtime=TelemetryPlannerRuntimeWrapper(
            build_interactive_planner_runtime(io_handler, ctx), telemetry, trace
        ),
        goal_init=build_goal_init_usecase(ctx), validator=ValidateAgainstSpec(spec),
        project_state=build_project_state_adapter(ctx), agent_registry=build_agent_registry(ctx),
        goal_repo=build_goal_repo(ctx), spec_repo=build_spec_repo(ctx),
        project_name=ctx.machine.project_name,
    )


def build_project_plan_repo(ctx: SettingsContext | None = None):
    ctx = ctx or _load_context()
    if ctx.machine.mode == "dry-run":
        from src.infra.fs.project_plan_repository import InMemoryProjectPlanRepository
        return InMemoryProjectPlanRepository()
    from src.infra.fs.project_plan_repository import YamlProjectPlanRepository
    return YamlProjectPlanRepository(build_project_paths(ctx).plan_path)


def build_planner_context_assembler(ctx: SettingsContext | None = None):
    from src.app.services.planner_context import PlannerContextAssembler
    ctx = ctx or _load_context()
    spec = build_load_project_spec(ctx).execute(ctx.machine.project_name)
    return PlannerContextAssembler(
        spec=spec, project_state=build_project_state_adapter(ctx),
        goal_repo=build_goal_repo(ctx), task_repo=build_task_repo(ctx),
        plan_repo=build_project_plan_repo(ctx),
    )


def build_planner_session_repo(ctx: SettingsContext | None = None):
    ctx = ctx or _load_context()
    if ctx.machine.mode == "dry-run":
        from src.infra.fs.planner_session_repository import InMemoryPlannerSessionRepository
        return InMemoryPlannerSessionRepository()
    from src.infra.fs.planner_session_repository import YamlPlannerSessionRepository
    return YamlPlannerSessionRepository(build_project_paths(ctx).planner_sessions_dir)


def build_planner_runtime(ctx: SettingsContext | None = None):
    ctx = ctx or _load_context()
    if ctx.machine.mode == "dry-run":
        from src.infra.runtime.planner_runtime import StubPlannerRuntime
        return StubPlannerRuntime()
    from src.infra.runtime.planner_runtime import AnthropicPlannerRuntime
    return AnthropicPlannerRuntime(api_key=ctx.secrets.anthropic_api_key)
