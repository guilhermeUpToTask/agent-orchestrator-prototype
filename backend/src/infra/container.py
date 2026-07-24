"""AppContainer — the composition root (rebuilt during the integration).

Runtime selection (composition-root config):
  PROJECT_REPO_DIR      target repo for the git-branching workspace
                        (defaults to <orchestrator_home>/workspace-repo)

Neither the REASONER nor the AGENT RUNNER is env-selected — both resolve from
SQLite:
  reasoner       config key reasoner.mode (stub|llm) + the providers catalog +
                 the envelope-encrypted secret store
                 (src/infra/reasoner/factory.py).
  agent_runner   config key agent_runner.mode (dry-run|real); in real mode
                 each task resolves through the AGENT REGISTRY — the bound
                 AgentSpec's runtime_type + provider/model catalog rows
                 (src/infra/runtime/factory.py).

Environment is read ONLY here (the composition root) — never deep in the code.
"""

from __future__ import annotations

import os
from functools import cached_property
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.app.ports import AgentRunner, Clock, Reasoner, Sandbox
from src.infra.clock import SystemClock
from src.infra.db.engine import build_engine, db_url_for_home, make_session_factory
from src.infra.db.observation_repository import SqliteProcessObservationRepository
from src.infra.db.reference_repos import (
    SqliteAgentRepository,
    SqliteCapabilityRepository,
    SqliteConfigStore,
    SqliteModelProviderRepository,
    SqliteModelRepository,
    SqliteProjectRepository,
)
from src.infra.db.agent_event_reader import SqliteAgentEventReader
from src.infra.db.agent_event_sink import SqliteAgentEventSink
from src.infra.db.chat_repository import SqliteChatRepository
from src.infra.db.secret_store import SqliteSecretStore, load_master_key
from src.infra.db.unit_of_work import SqliteUnitOfWork
from src.infra.git.project_workspace import (
    ProjectRoutingWorkspace,
    ProjectWorkspaceResolver,
)
from src.domain.policies.retry_policies import RetryPolicy
from src.infra.policies.retry_policy_factory import build_retry_policy
from src.infra.reasoner.factory import build_reasoner
from src.infra.runtime.factory import build_agent_runner
from src.infra.runtime.sandbox import NoSandbox
from src.infra.runtime.verification_executor import LocalVerificationExecutor


class AppContainer:
    """Lazy composition root: each dependency is constructed at most once per
    container instance, only when actually needed."""

    def __init__(self, orchestrator_home: Path) -> None:
        self.orchestrator_home = orchestrator_home

    @classmethod
    def from_env(cls) -> "AppContainer":
        home = Path(os.environ.get("ORCHESTRATOR_HOME", str(Path.home() / ".orchestrator")))
        return cls(orchestrator_home=home)

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
        via `orchestrate config set` and apply to the next created plan
        without an API restart."""
        return build_retry_policy(self.config_store)

    @cached_property
    def secret_store(self) -> SqliteSecretStore:
        # fail-closed: a missing/invalid ORCHESTRATOR_MASTER_KEY raises here
        return SqliteSecretStore(self.session_factory, load_master_key())

    # --- Stage 5: execution adapters ---
    @cached_property
    def workspace_resolver(self) -> ProjectWorkspaceResolver:
        return ProjectWorkspaceResolver(self.project_repo, self.orchestrator_home)

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
    def agent_runner(self) -> AgentRunner:
        """Catalog-resolved: config key agent_runner.mode selects dry-run
        (default, no secrets needed — the dummy IS the dry-run runtime, same
        FailureKind taxonomy as the real CLI runners) or real (per-task
        resolution through the agent registry's runtime_type + provider/model
        rows). The secret store is passed as a thunk so dry-run never
        constructs it (it fails closed on a missing ORCHESTRATOR_MASTER_KEY)."""
        return build_agent_runner(
            self.config_store,
            self.provider_repo,
            self.model_repo,
            lambda: self.secret_store,
            self.orchestrator_home,
            self.observation_repository,
            self.sandbox,
        )

    @cached_property
    def verification_executor(self) -> LocalVerificationExecutor:
        return LocalVerificationExecutor(self.clock)

    # --- Stage 6: the planning reasoner ---
    @cached_property
    def reasoner(self) -> Reasoner:
        """Catalog-resolved: config key reasoner.mode selects stub (default,
        no secrets needed) or llm (providers/models/secret-store resolution).
        The secret store is passed as a thunk so stub mode never constructs it
        (it fails closed on a missing ORCHESTRATOR_MASTER_KEY)."""
        return build_reasoner(
            self.config_store,
            self.provider_repo,
            self.model_repo,
            lambda: self.secret_store,
            self.capability_repo,
            self.observation_repository,
        )
