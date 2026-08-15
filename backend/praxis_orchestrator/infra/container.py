"""AppContainer — the composition root (rebuilt during the integration).

Repository routing is NOT env-selected either. This container does not read
PROJECT_REPO_DIR — locked by tests/unit/test_fixture_docs_contract.py. A plan
reaches a repository through its project: `ProjectDefinition.repo_url`, resolved
by `infra/git/project_workspace.py` and validated on write by
`infra/git/repository_binding.py`. A project WITHOUT a repo_url gets a scratch
repository under <orchestrator_home>/projects/<id>/repo; a project that names
one and gets it wrong is refused rather than silently given an empty repo.

Neither the REASONER nor the AGENT RUNNER is env-selected — both resolve from
SQLite:
  reasoner       config key reasoner.mode (stub|llm) + the providers catalog +
                 the envelope-encrypted secret store
                 (praxis_orchestrator/infra/reasoner/factory.py).
  agent_runner   config key agent_runner.mode (dry-run|real); in real mode
                 each task resolves through the AGENT REGISTRY — the bound
                 AgentSpec's runtime_type + provider/model catalog rows
                 (praxis_orchestrator/infra/runtime/factory.py).

Environment is read ONLY here (the composition root) — never deep in the code.
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from praxis_orchestrator.app.ports import AgentRunner, Clock, Reasoner, Sandbox
from praxis_orchestrator.infra.clock import SystemClock
from praxis_orchestrator.infra.db.engine import build_engine, db_url_for_home, make_session_factory
from praxis_orchestrator.infra.state_home import resolve_home
from praxis_orchestrator.infra.db.observation_repository import SqliteProcessObservationRepository
from praxis_orchestrator.infra.db.reference_repos import (
    SqliteAgentRepository,
    SqliteCapabilityRepository,
    SqliteConfigStore,
    SqliteModelProviderRepository,
    SqliteModelRepository,
    SqliteProjectRepository,
)
from praxis_orchestrator.infra.db.agent_event_reader import SqliteAgentEventReader
from praxis_orchestrator.infra.db.attempt_feedback_repository import SqliteAttemptFeedbackRepository
from praxis_orchestrator.infra.db.planning_artifact_repository import SqlitePlanningArtifactRepository
from praxis_orchestrator.infra.db.agent_event_sink import SqliteAgentEventSink
from praxis_orchestrator.infra.db.chat_repository import SqliteChatRepository
from praxis_orchestrator.infra.db.secret_store import SqliteSecretStore, load_master_key
from praxis_orchestrator.infra.db.unit_of_work import SqliteUnitOfWork
from praxis_orchestrator.infra.db.worker_registry import WorkerRegistry
from praxis_orchestrator.infra.git.repository_reader import GitRepositoryReader
from praxis_orchestrator.infra.git.project_workspace import (
    ProjectRoutingWorkspace,
    ProjectWorkspaceResolver,
)
from praxis_orchestrator.domain.policies.retry_policies import RetryPolicy
from praxis_orchestrator.app.provider_capacity import ProviderCapacityPolicy, RoutingPolicy
from praxis_orchestrator.infra.policies.provider_capacity_factory import (
    build_provider_capacity_policy,
    build_routing_policy,
)
from praxis_orchestrator.infra.policies.retry_policy_factory import build_retry_policy
from praxis_orchestrator.infra.reasoner.factory import build_reasoner
from praxis_orchestrator.infra.reasoner.live_reasoner import LiveReasoner
from praxis_orchestrator.infra.runtime.factory import build_agent_runner
from praxis_orchestrator.app.environment_port import EnvironmentSpec, ProjectEnvironment
from praxis_orchestrator.app.forge_port import ForgePort
from praxis_orchestrator.infra.db.secret_ref import SecretRef
from praxis_orchestrator.app.execution_services import ExecutionServices
from praxis_orchestrator.infra.environment.container_environment import ContainerEnvironment
from praxis_orchestrator.infra.environment.no_environment import NoEnvironment
from praxis_orchestrator.infra.environment.spec import (
    read_container_binary,
    read_environment_spec,
)
from praxis_orchestrator.infra.forge.binding import read_binding
from praxis_orchestrator.infra.forge.github import GitHubForge
from praxis_orchestrator.infra.forge.no_forge import NoForge
from praxis_orchestrator.infra.runtime.sandbox import NoSandbox
from praxis_orchestrator.infra.runtime.verification_executor import LocalVerificationExecutor


class AppContainer:
    """Lazy composition root: each dependency is constructed at most once per
    container instance, only when actually needed."""

    def __init__(self, orchestrator_home: Path) -> None:
        self.orchestrator_home = orchestrator_home

    @classmethod
    def from_env(cls) -> "AppContainer":
        # `PRAXIS_HOME`, else `~/.praxis`. See infra/state_home.py.
        return cls(orchestrator_home=resolve_home())

    # --- Stage 3: persistence core ---
    @cached_property
    def engine(self) -> Engine:
        return build_engine(db_url_for_home(self.orchestrator_home))

    @cached_property
    def session_factory(self) -> sessionmaker[Session]:
        return make_session_factory(self.engine)

    @cached_property
    def clock(self) -> Clock:
        return SystemClock()

    def new_unit_of_work(self) -> SqliteUnitOfWork:
        """One UoW per worker/request — the instance is not thread-safe."""
        return SqliteUnitOfWork(self.session_factory, self.clock)

    # --- Stage 4: reference data, config, secrets ---
    @cached_property
    def agent_repo(self) -> SqliteAgentRepository:
        return SqliteAgentRepository(self.session_factory)

    @cached_property
    def capability_repo(self) -> SqliteCapabilityRepository:
        return SqliteCapabilityRepository(self.session_factory)

    @cached_property
    def provider_repo(self) -> SqliteModelProviderRepository:
        return SqliteModelProviderRepository(self.session_factory)

    @cached_property
    def model_repo(self) -> SqliteModelRepository:
        return SqliteModelRepository(self.session_factory)

    @cached_property
    def project_repo(self) -> SqliteProjectRepository:
        return SqliteProjectRepository(self.session_factory)

    @cached_property
    def config_store(self) -> SqliteConfigStore:
        return SqliteConfigStore(self.session_factory)

    @property
    def default_retry_policy(self) -> RetryPolicy:
        """Read fresh on every access (deliberately NOT a cached_property):
        the config keys behind it (execution.retry_*) are meant to be tuned
        via `praxis config set` and apply to the next created plan
        without an API restart."""
        return build_retry_policy(self.config_store)

    @property
    def provider_capacity_policy(self) -> ProviderCapacityPolicy:
        """Read fresh on every access, same reasoning as default_retry_policy:
        the ceilings behind it are operator-tuned via `praxis config set`
        and must apply without an API restart."""
        return build_provider_capacity_policy(self.config_store)

    @property
    def routing_policy(self) -> RoutingPolicy:
        """Read fresh per access, same reasoning as the other two policies."""
        return build_routing_policy(self.config_store)

    @cached_property
    def secret_store(self) -> SqliteSecretStore:
        # fail-closed: a missing/invalid PRAXIS_MASTER_KEY raises here
        return SqliteSecretStore(self.session_factory, load_master_key())

    # --- Stage 5: execution adapters ---
    @cached_property
    def workspace_resolver(self) -> ProjectWorkspaceResolver:
        return ProjectWorkspaceResolver(self.project_repo, self.orchestrator_home)

    @cached_property
    def repository_reader(self) -> GitRepositoryReader:
        """Read-only repository sight for the planning reasoner. Shares the
        project resolver with the execution workspace, so the planner reads the
        exact repository the agents will edit."""
        return GitRepositoryReader(self.workspace_resolver)

    @cached_property
    def workspace(self) -> ProjectRoutingWorkspace:
        return ProjectRoutingWorkspace(self.new_unit_of_work, self.workspace_resolver)

    @cached_property
    def agent_event_sink(self) -> SqliteAgentEventSink:
        return SqliteAgentEventSink(self.session_factory)

    @cached_property
    def agent_event_reader(self) -> SqliteAgentEventReader:
        return SqliteAgentEventReader(self.session_factory)

    @cached_property
    def worker_registry(self) -> WorkerRegistry:
        return WorkerRegistry(self.session_factory)

    @cached_property
    def observation_repository(self) -> SqliteProcessObservationRepository:
        return SqliteProcessObservationRepository(self.session_factory, self.clock)

    @cached_property
    def chat_store(self) -> SqliteChatRepository:
        return SqliteChatRepository(self.session_factory)

    @cached_property
    def sandbox(self) -> Sandbox:
        """ROADMAP item 33: NoSandbox is today's behavior and the permanent
        fallback — a real adapter (e.g. BubblewrapSandbox, item 34) is a
        drop-in swap here, not a change to any caller."""
        return NoSandbox()

    @cached_property
    def environment(self) -> ProjectEnvironment:
        """The cycle acceptance run (P8.2/P8.5). `NoEnvironment` remains the
        PERMANENT fallback, like `NoSandbox` and `NoForge` — most projects are
        libraries and CLIs whose tests genuinely are the contract, and for those
        an acceptance run has nothing to add.

        `environment.mode = container` selects the real adapter; the container
        CLI itself is a separate key, because which runtime exists is a property
        of the machine and not of the project. Anything else falls back rather
        than raising: an acceptance run is advisory, so a typo here must not
        take down the promotion or publication gate it was only observing.
        """
        mode = (self.config_store.get("orchestrator", "environment.mode") or "").strip()
        if mode != "container":
            return NoEnvironment()
        return ContainerEnvironment(binary=read_container_binary(self.config_store))

    def environment_context(self, plan_id: str) -> tuple[Path, EnvironmentSpec | None]:
        """Where a plan's repository is, and how the operator says to boot it.

        A container method rather than a `Workspace` port method, because
        `Workspace` is a FROZEN domain port and resolving a project's checkout
        is a composition-root job this container already does for readiness and
        for publication.

        The spec comes from the project-scoped config store — the same door the
        forge binding uses, and for the same reason: no domain entity changes.
        `None` means the operator has configured nothing, which `NoEnvironment`
        reports as `skipped` rather than as a pass.
        """
        with self.new_unit_of_work() as uow:
            plan = uow.plans.get(plan_id)
        project_id = plan.project_id
        if project_id is None:
            return Path("."), None
        project = self.project_repo.get(project_id)
        repo = self.workspace_resolver.repository_path_for(project)
        return repo, read_environment_spec(self.config_store, project_id)

    def forge_for(self, project_id: str) -> ForgePort:
        """The forge bound to this project, or the permanent no-forge fallback.

        A method rather than a cached_property: it takes an argument, and the
        binding must be re-read per call for the same reason `reasoner` became
        a LiveReasoner — a token bound in Settings has to land on the next
        publication, not the next worker restart.
        """
        binding = read_binding(self.config_store, project_id)
        if binding is None:
            return NoForge()
        token = self.secret_store.resolve(SecretRef(uri=binding.token_ref))
        return GitHubForge(binding.repository, token)

    @cached_property
    def agent_runner(self) -> AgentRunner:
        """Catalog-resolved: config key agent_runner.mode selects dry-run
        (default, no secrets needed — the dummy IS the dry-run runtime, same
        FailureKind taxonomy as the real CLI runners) or real (per-task
        resolution through the agent registry's runtime_type + provider/model
        rows). The secret store is passed as a thunk so dry-run never
        constructs it (it fails closed on a missing PRAXIS_MASTER_KEY)."""
        return build_agent_runner(
            self.config_store,
            self.provider_repo,
            self.model_repo,
            lambda: self.secret_store,
            self.orchestrator_home,
            self.observation_repository,
            self.sandbox,
            self.attempt_feedback,
        )

    @cached_property
    def planning_artifacts(self) -> SqlitePlanningArtifactRepository:
        """Failed planning attempts, kept so a retry starts better informed.
        Own short transactions — it must outlive the transaction that failed."""
        return SqlitePlanningArtifactRepository(self.session_factory)

    @cached_property
    def attempt_feedback(self) -> SqliteAttemptFeedbackRepository:
        """Why the previous candidate was rejected. Own short read transaction —
        the agent runner executes outside the plan UnitOfWork by design."""
        return SqliteAttemptFeedbackRepository(self.session_factory)

    @cached_property
    def verification_executor(self) -> LocalVerificationExecutor:
        return LocalVerificationExecutor(self.clock)

    @property
    def execution_services(self) -> ExecutionServices:
        """Every collaborator an execution drive needs, enumerated in ONE place
        (P8.7 task 4).

        It used to be enumerated at four call sites, and two of them were wrong:
        the worker never passed `environment`/`environment_context` (so the
        acceptance run never fired in production) and `PlanDispatcher` never
        passed `routing`. Adding a collaborator here now reaches every driver,
        which is the whole point.

        A plain property, NOT cached: `provider_capacity_policy` and
        `routing_policy` are deliberately re-read per access so
        `praxis config set` applies without a restart, and caching the
        bundle would pin both to whatever the process started with — quietly
        undoing the thing those two docstrings promise.
        """
        return ExecutionServices(
            runner=self.agent_runner,
            agents=self.agent_repo,
            workspace=self.workspace,
            event_sink=self.agent_event_sink,
            clock=self.clock,
            verifier=self.verification_executor,
            capacity=self.provider_capacity_policy,
            providers=self.provider_repo,
            routing=self.routing_policy,
            repository_reader=self.repository_reader,
            planning_artifacts=self.planning_artifacts,
            environment=self.environment,
            environment_context=self.environment_context,
        )

    # --- Stage 6: the planning reasoner ---
    @cached_property
    def reasoner(self) -> Reasoner:
        """A LIVE view of the configured reasoner: every call re-resolves it.

        Catalog-resolved: config key reasoner.mode selects stub (default, no
        secrets needed) or llm (providers/models/secret-store resolution).

        The wrapper is what makes `reasoner.*` config actually configurable. The
        worker builds its `PlanningHandler` once at boot and holds the instance
        it was given, so resolving eagerly here — cached or not — pinned every
        key to whatever the process started with, and a successful config write
        did nothing until a restart. Resolving per call costs a config read and
        a key decrypt against an LLM round trip. See `live_reasoner.py`.
        """
        return LiveReasoner(self._build_reasoner)

    def _build_reasoner(self) -> Reasoner:
        """The secret store is passed as a thunk so stub mode never constructs
        it (it fails closed on a missing PRAXIS_MASTER_KEY)."""
        return build_reasoner(
            self.config_store,
            self.provider_repo,
            self.model_repo,
            lambda: self.secret_store,
            self.capability_repo,
            self.observation_repository,
            self.repository_reader,
            self.planning_artifacts,
        )
