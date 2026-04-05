"""
src/infra/container.py — Application container.

Built ONCE at the CLI entry point and passed explicitly to every command.
Uses functools.cached_property so each dependency is constructed at most once
per container instance — lazily, only when actually needed.

Pattern:
    # At the CLI entry point
    app = AppContainer.from_env()

    # Inside a command
    usecase = app.goal_init_usecase
    result  = usecase.execute(spec)

No builder ever calls SettingsService().load() on its own — the container
holds the single loaded SettingsContext and derives everything from it.
"""

from __future__ import annotations

from functools import cached_property
from typing import Callable

import structlog

from src.infra.settings import SettingsContext, SettingsService
from src.infra.settings.models import ConfigurationError
from src.infra.project_paths import ProjectPaths

log = structlog.get_logger(__name__)


class AppContainer:
    """
    Dependency-injection container for one CLI invocation.
    All properties are lazy (cached_property) — nothing is built until
    first access, and each thing is built at most once.
    """

    def __init__(self, ctx: SettingsContext) -> None:
        self._ctx = ctx

        # Elegantly shield against missing directories on fresh machines.
        # This silently ensures ~/.orchestrator exists immediately.
        self._ctx.machine.orchestrator_home.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Entry-point factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, project_name: str | None = None) -> "AppContainer":
        """Load settings from environment + disk and return a container."""
        ctx = SettingsService().load(project_name=project_name)
        return cls(ctx)

    # ------------------------------------------------------------------
    # Context + paths (the two roots everything else derives from)
    # ------------------------------------------------------------------

    @property
    def ctx(self) -> SettingsContext:
        return self._ctx

    @cached_property
    def paths(self) -> ProjectPaths:
        return self._ctx.paths  # cached_property on SettingsContext

    def get_required_project(self) -> str:
        """Return project_name or raise with an actionable message."""
        name = self._ctx.machine.project_name
        if not name:
            raise ConfigurationError("No project configured.\nRun: orchestrator init")
        return name

    # ------------------------------------------------------------------
    # Infrastructure: Redis
    # ------------------------------------------------------------------

    @cached_property
    def _redis(self):
        import redis

        return redis.from_url(self._ctx.machine.redis_url, decode_responses=False)

    # ------------------------------------------------------------------
    # Infrastructure: repositories
    # ------------------------------------------------------------------

    @cached_property
    def task_repo(self):
        from src.infra.fs.task_repository import YamlTaskRepository

        return YamlTaskRepository(self.paths.tasks_dir)

    @cached_property
    def goal_repo(self):
        from src.infra.fs.goal_repository import YamlGoalRepository

        return YamlGoalRepository(self.paths.goals_dir)

    @cached_property
    def agent_registry(self):
        from src.infra.fs.agent_registry import JsonAgentRegistry

        return JsonAgentRegistry(self.paths.registry_path)

    @cached_property
    def project_plan_repo(self):
        if self._ctx.machine.mode == "dry-run":
            from src.infra.fs.project_plan_repository import InMemoryProjectPlanRepository

            return InMemoryProjectPlanRepository()
        from src.infra.fs.project_plan_repository import YamlProjectPlanRepository

        return YamlProjectPlanRepository(self.paths.plan_path)

    @cached_property
    def planner_session_repo(self):
        if self._ctx.machine.mode == "dry-run":
            from src.infra.fs.planner_session_repository import InMemoryPlannerSessionRepository

            return InMemoryPlannerSessionRepository()
        from src.infra.fs.planner_session_repository import YamlPlannerSessionRepository

        return YamlPlannerSessionRepository(self.paths.planner_sessions_dir)

    @cached_property
    def spec_repo(self):
        from src.infra.fs.project_spec_repository import FileProjectSpecRepository

        return FileProjectSpecRepository(orchestrator_home=self._ctx.machine.orchestrator_home)

    # ------------------------------------------------------------------
    # Infrastructure: ports
    # ------------------------------------------------------------------

    @cached_property
    def event_port(self):
        if self._ctx.machine.mode == "dry-run":
            from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter

            return InMemoryEventAdapter()
        from src.infra.redis_adapters.event_adapter import RedisEventAdapter

        return RedisEventAdapter(
            self._redis,
            journal_dir=str(self.paths.events_dir),
        )

    @cached_property
    def lease_port(self):
        if self._ctx.machine.mode == "dry-run":
            from src.infra.redis_adapters.lease_memory import InMemoryLeaseAdapter

            return InMemoryLeaseAdapter()
        from src.infra.redis_adapters.lease_adapter import RedisLeaseAdapter

        return RedisLeaseAdapter(self._redis)

    @cached_property
    def telemetry_emitter(self):
        if self._ctx.machine.mode == "dry-run":
            from src.infra.redis_adapters.telemetry_adapter import InMemoryTelemetryEmitter

            return InMemoryTelemetryEmitter()
        from src.infra.redis_adapters.telemetry_adapter import (
            CompositeTelemetryEmitter,
            FileTelemetryLogEmitter,
            JsonLoggerTelemetryEmitter,
            RedisTelemetryEmitter,
        )

        return CompositeTelemetryEmitter(
            [
                RedisTelemetryEmitter(self._redis, journal_dir=self.paths.telemetry_events_dir),
                JsonLoggerTelemetryEmitter(),
                FileTelemetryLogEmitter(self.paths.telemetry_logs_dir / "telemetry.jsonl"),
            ]
        )

    @cached_property
    def git_workspace(self):
        if self._ctx.machine.mode == "dry-run":
            from src.infra.git.workspace_adapter import DryRunGitWorkspaceAdapter

            return DryRunGitWorkspaceAdapter()
        from src.infra.git.workspace_adapter import GitWorkspaceAdapter

        return GitWorkspaceAdapter(
            workspace_base=self.paths.workspace_dir,
            source_repo_url=self._ctx.project.source_repo_url,
        )

    @cached_property
    def github_client(self):
        """
        GitHub adapter. Raises ConfigurationError if real mode requires
        credentials that aren't set — fail fast, clear message.
        """
        if self._ctx.machine.mode == "dry-run":
            from src.infra.github.client import StubGitHubClient

            return StubGitHubClient()

        from src.infra.github.client import GitHubClient

        token = self._ctx.secrets.require_github_token()
        owner = self._ctx.project.github_owner or ""
        repo = self._ctx.project.github_repo or ""

        if not all([owner, repo]):
            log.warning(
                "container.github_not_configured",
                hint="Run `orchestrate init` to set github_owner / github_repo.",
            )
            from src.infra.github.client import StubGitHubClient

            return StubGitHubClient()

        return GitHubClient(token=token, owner=owner, repo=repo)

    # ------------------------------------------------------------------
    # Infrastructure: runtimes
    # ------------------------------------------------------------------

    @cached_property
    def runtime_factory(self) -> Callable:
        from src.infra.runtime.factory import build_runtime_factory

        return build_runtime_factory(self._ctx)

    @cached_property
    def planner_runtime(self):
        if self._ctx.machine.mode == "dry-run":
            from src.infra.runtime.planners.anthropic_planner_runtime import StubPlannerRuntime

            return StubPlannerRuntime()

        from src.infra.runtime.planners.planner_factory import get_planner_strategy

        strategy = get_planner_strategy(self._ctx)
        return strategy.build_autonomous(self._ctx)

    @cached_property
    def interactive_planner_runtime(self):
        if self._ctx.machine.mode == "dry-run":
            from src.infra.runtime.planners.anthropic_interactive_planner_runtime import (
                StubInteractivePlannerRuntime,
            )

            return StubInteractivePlannerRuntime()

        from src.infra.runtime.planners.planner_factory import get_planner_strategy

        strategy = get_planner_strategy(self._ctx)
        return strategy.build_interactive(self._ctx)

    # ------------------------------------------------------------------
    # Infrastructure: misc
    # ------------------------------------------------------------------

    @cached_property
    def logs_adapter(self):
        from src.infra.logs_and_tests import FilesystemTaskLogsAdapter

        return FilesystemTaskLogsAdapter(logs_base=self.paths.logs_dir)

    @cached_property
    def test_runner(self):
        from src.infra.logs_and_tests import SubprocessTestRunnerAdapter

        return SubprocessTestRunnerAdapter()

    @cached_property
    def lease_refresher_factory(self):
        from src.infra.redis_adapters.lease_refresher import LeaseRefresher

        return lambda lease_port, lease_token: LeaseRefresher(
            lease_port=lease_port,
            lease_token=lease_token,
        )

    # ------------------------------------------------------------------
    # Application services
    # ------------------------------------------------------------------

    @cached_property
    def task_creation_service(self):
        from src.app.services.task_creation import TaskCreationService

        return TaskCreationService(
            task_repo=self.task_repo,
            event_port=self.event_port,
        )

    @cached_property
    def project_state(self):
        from src.app.telemetry.project_state_wrapper import TelemetryProjectStateAdapter
        from src.app.telemetry.service import TelemetryService

        if self._ctx.machine.mode == "dry-run":
            from src.infra.fs.project_state_adapter import InMemoryProjectStateAdapter

            base = InMemoryProjectStateAdapter()
        else:
            from src.infra.fs.project_state_adapter import FilesystemProjectStateAdapter

            base = FilesystemProjectStateAdapter(state_dir=self.paths.project_state_dir)

        telemetry = TelemetryService(self.telemetry_emitter, producer="project-state")
        trace = telemetry.start_trace(correlation_id=self.get_required_project())
        return TelemetryProjectStateAdapter(base, telemetry=telemetry, trace_context=trace)

    # ------------------------------------------------------------------
    # Use cases
    # ------------------------------------------------------------------

    @cached_property
    def task_manager_handler(self):
        from src.app.handlers.task_manager import TaskManagerHandler
        from src.domain import SchedulerService

        return TaskManagerHandler(
            task_repo=self.task_repo,
            agent_registry=self.agent_registry,
            event_port=self.event_port,
            lease_port=self.lease_port,
            scheduler=SchedulerService(),
        )

    # @cached_property
    def get_worker_handler(self, agent_id: str):
        """Build the worker handler for a specific agent identity."""
        from src.app.handlers.worker import WorkerHandler

        return WorkerHandler(
            agent_id=agent_id,
            repo_url=self.paths.repo_url,
            task_repo=self.task_repo,
            agent_registry=self.agent_registry,
            event_port=self.event_port,
            lease_port=self.lease_port,
            git_workspace=self.git_workspace,
            runtime_factory=self.runtime_factory,
            logs_port=self.logs_adapter,
            test_runner=self.test_runner,
            lease_refresher_factory=self.lease_refresher_factory,
            task_timeout_seconds=self._ctx.machine.task_timeout,
        )

    @cached_property
    def task_execute_usecase(self):
        from src.app.usecases.task_execute import TaskExecuteUseCase

        return TaskExecuteUseCase(
            repo_url=self.paths.repo_url,
            task_repo=self.task_repo,
            agent_registry=self.agent_registry,
            event_port=self.event_port,
            lease_port=self.lease_port,
            git_workspace=self.git_workspace,
            runtime_factory=self.runtime_factory,
            logs_port=self.logs_adapter,
            test_runner=self.test_runner,
            telemetry_emitter=self.telemetry_emitter,
            lease_refresher_factory=self.lease_refresher_factory,
            task_timeout_seconds=self._ctx.machine.task_timeout,
        )

    @cached_property
    def task_assign_usecase(self):
        from src.app.usecases.task_assign import TaskAssignUseCase
        from src.domain import SchedulerService

        return TaskAssignUseCase(
            task_repo=self.task_repo,
            agent_registry=self.agent_registry,
            event_port=self.event_port,
            lease_port=self.lease_port,
            scheduler=SchedulerService(),
        )

    @cached_property
    def task_retry_usecase(self):
        from src.app.usecases.task_retry import TaskRetryUseCase

        return TaskRetryUseCase(task_repo=self.task_repo, event_port=self.event_port)

    @cached_property
    def task_delete_usecase(self):
        from src.app.usecases.task_delete import TaskDeleteUseCase

        return TaskDeleteUseCase(task_repo=self.task_repo)

    @cached_property
    def task_prune_usecase(self):
        from src.app.usecases.task_prune import TaskPruneUseCase

        return TaskPruneUseCase(task_repo=self.task_repo)

    @cached_property
    def task_unblock_usecase(self):
        from src.app.usecases.task_unblock import TaskUnblockUseCase

        return TaskUnblockUseCase(
            task_repo=self.task_repo,
            assign_usecase=self.task_assign_usecase,
        )

    @cached_property
    def task_fail_handling_usecase(self):
        from src.app.usecases.task_fail_handling import TaskFailHandlingUseCase

        return TaskFailHandlingUseCase(task_repo=self.task_repo, event_port=self.event_port)

    @cached_property
    def agent_register_usecase(self):
        from src.app.usecases.agent_register import AgentRegisterUseCase

        return AgentRegisterUseCase(agent_registry=self.agent_registry)

    @cached_property
    def project_reset_usecase(self):
        from src.app.usecases.project_reset import ProjectResetUseCase

        return ProjectResetUseCase(
            task_repo=self.task_repo,
            lease_port=self.lease_port,
            agent_registry=self.agent_registry,
            repo_url=self.paths.repo_url,
        )

    @cached_property
    def goal_init_usecase(self):
        from src.app.usecases.goal_init import GoalInitUseCase

        return GoalInitUseCase(
            goal_repo=self.goal_repo,
            task_repo=self.task_repo,
            event_port=self.event_port,
            git_workspace=self.git_workspace,
            task_creation=self.task_creation_service,
            repo_url=self.paths.repo_url,
        )

    @cached_property
    def goal_merge_task_usecase(self):
        from src.app.usecases.goal_merge_task import GoalMergeTaskUseCase

        return GoalMergeTaskUseCase(
            task_repo=self.task_repo,
            goal_repo=self.goal_repo,
            event_port=self.event_port,
            git_workspace=self.git_workspace,
            repo_url=self.paths.repo_url,
            telemetry_emitter=self.telemetry_emitter,
        )

    @cached_property
    def goal_cancel_task_usecase(self):
        from src.app.usecases.goal_cancel_task import GoalCancelTaskUseCase

        return GoalCancelTaskUseCase(
            task_repo=self.task_repo,
            goal_repo=self.goal_repo,
            event_port=self.event_port,
            telemetry_emitter=self.telemetry_emitter,
        )

    @cached_property
    def goal_finalize_usecase(self):
        from src.app.usecases.goal_finalize import GoalFinalizeUseCase

        return GoalFinalizeUseCase(goal_repo=self.goal_repo, event_port=self.event_port)

    @cached_property
    def unblock_goals_usecase(self):
        from src.app.usecases.unblock_goals import UnblockGoalsUseCase

        return UnblockGoalsUseCase(goal_repo=self.goal_repo, event_port=self.event_port)

    @cached_property
    def advance_goal_from_pr_usecase(self):
        from src.app.usecases.advance_goal_from_pr import AdvanceGoalFromPRUseCase

        return AdvanceGoalFromPRUseCase(
            goal_repo=self.goal_repo,
            event_port=self.event_port,
            unblock_goals_usecase=self.unblock_goals_usecase,
            plan_repo=self.project_plan_repo,
        )

    @cached_property
    def create_goal_pr_usecase(self):
        from src.app.usecases.create_goal_pr import CreateGoalPRUseCase

        base_branch = self._ctx.project.github_base_branch
        return CreateGoalPRUseCase(
            goal_repo=self.goal_repo,
            event_port=self.event_port,
            github=self.github_client,
            base_branch=base_branch,
        )

    @cached_property
    def sync_goal_pr_usecase(self):
        from src.app.usecases.sync_goal_pr_status import SyncGoalPRStatusUseCase

        spec = None
        try:
            spec = self.load_project_spec_usecase.execute(self.get_required_project())
        except Exception:
            pass
        return SyncGoalPRStatusUseCase(
            goal_repo=self.goal_repo,
            event_port=self.event_port,
            github=self.github_client,
            spec=spec,
        )

    @cached_property
    def load_project_spec_usecase(self):
        from src.app.usecases.load_project_spec import LoadProjectSpec

        return LoadProjectSpec(spec_repo=self.spec_repo)

    @cached_property
    def validate_against_spec_usecase(self):
        from src.app.usecases.validate_against_spec import ValidateAgainstSpec

        spec = self.load_project_spec_usecase.execute(self.get_required_project())
        return ValidateAgainstSpec(spec)

    @cached_property
    def propose_spec_change_usecase(self):
        from src.app.usecases.propose_spec_change import ProposeSpecChange

        return ProposeSpecChange(spec_repo=self.spec_repo)

    @cached_property
    def plan_goal_tasks_usecase(self):
        from src.app.usecases.plan_goal_tasks import PlanGoalTasksUseCase

        return PlanGoalTasksUseCase(
            task_creation=self.task_creation_service,
            goal_repo=self.goal_repo,
            planner_runtime=self.planner_runtime,
            event_port=self.event_port,
            spec_repo=self.spec_repo,
        )

    @cached_property
    def task_graph_orchestrator(self):
        from src.app.orchestrator import TaskGraphOrchestrator

        return TaskGraphOrchestrator(
            task_repo=self.task_repo,
            goal_repo=self.goal_repo,
            event_port=self.event_port,
            merge_usecase=self.goal_merge_task_usecase,
            cancel_usecase=self.goal_cancel_task_usecase,
            spec_repo=self.spec_repo,
            project_name=self.get_required_project(),
            create_pr_usecase=None,
            telemetry_emitter=self.telemetry_emitter,
            plan_goal_tasks=self.plan_goal_tasks_usecase,
        )

    @cached_property
    def task_graph_orchestrator_with_pr(self):
        from src.app.orchestrator import TaskGraphOrchestrator

        return TaskGraphOrchestrator(
            task_repo=self.task_repo,
            goal_repo=self.goal_repo,
            event_port=self.event_port,
            merge_usecase=self.goal_merge_task_usecase,
            cancel_usecase=self.goal_cancel_task_usecase,
            spec_repo=self.spec_repo,
            project_name=self.get_required_project(),
            create_pr_usecase=self.create_goal_pr_usecase,
            telemetry_emitter=self.telemetry_emitter,
            plan_goal_tasks=self.plan_goal_tasks_usecase,
        )

    def get_reconciler(self, interval_seconds: int = 60, stuck_task_min_age_seconds: int = 120):
        from src.app.reconciliation import Reconciler

        return Reconciler(
            task_repo=self.task_repo,
            lease_port=self.lease_port,
            event_port=self.event_port,
            agent_registry=self.agent_registry,
            interval_seconds=interval_seconds,
            stuck_task_min_age_seconds=stuck_task_min_age_seconds,
            goal_repo=self.goal_repo,
            sync_pr_usecase=self.sync_goal_pr_usecase,
            advance_pr_usecase=self.advance_goal_from_pr_usecase,
        )

    @cached_property
    def planner_context_assembler(self):
        from src.app.services.planner_context import PlannerContextAssembler

        spec = self.load_project_spec_usecase.execute(self.get_required_project())
        return PlannerContextAssembler(
            spec=spec,
            project_state=self.project_state,
            goal_repo=self.goal_repo,
            task_repo=self.task_repo,
            plan_repo=self.project_plan_repo,
        )

    @cached_property
    def planner_orchestrator(self):
        from src.app.usecases.planner_orchestrator import PlannerOrchestrator
        from src.app.usecases.validate_against_spec import ValidateAgainstSpec
        from src.app.telemetry.runtime_wrappers import TelemetryPlannerRuntimeWrapper
        from src.app.telemetry.service import TelemetryService

        project_name = self.get_required_project()

        spec = self.load_project_spec_usecase.execute(project_name)
        telemetry = TelemetryService(self.telemetry_emitter, producer="planner-orchestrator")
        trace = telemetry.start_trace(correlation_id=self._ctx.machine.project_name)
        return PlannerOrchestrator(
            plan_repo=self.project_plan_repo,
            session_repo=self.planner_session_repo,
            context_assembler=self.planner_context_assembler,
            autonomous_runtime=TelemetryPlannerRuntimeWrapper(
                self.planner_runtime, telemetry, trace
            ),
            interactive_runtime=TelemetryPlannerRuntimeWrapper(
                self.interactive_planner_runtime, telemetry, trace
            ),
            goal_init=self.goal_init_usecase,
            validator=ValidateAgainstSpec(spec),
            project_state=self.project_state,
            agent_registry=self.agent_registry,
            goal_repo=self.goal_repo,
            spec_repo=self.spec_repo,
            project_name=project_name,
            event_port=self.event_port,
        )
