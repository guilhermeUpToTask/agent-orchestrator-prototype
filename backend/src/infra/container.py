"""AppContainer — the composition root (rebuilt during the integration).

TRANSPLANT NOTE: the old container wired the pre-refactor domain (task manager /
goal orchestrator / reconciler daemons) and was emptied with them. It grows back
stage by stage as the real adapters land behind the new ports:

  Stage 3 — engine/session factory, SystemClock, SqliteUnitOfWork  [done]
  Stage 4 — reference-data repos, config store, secret store        [done]
  Stage 5 — workspace + agent-runner adapters, agent-event sink     [done]
  Stage 6 — reasoner (stub; real LLM is roadmap 2.5), worker wiring [done]
  Stage 7 — API dependency surface (SettingsService replaces the env read here)

Runtime selection (composition-root config, richer settings land in Stage 7):
  AGENT_MODE            dry-run (default) | pi | claude | gemini
  AGENT_MODEL           model id for the selected runtime
  PROJECT_REPO_DIR      target repo for the git-branching workspace
                        (defaults to <orchestrator_home>/workspace-repo)

Environment is read ONLY here (the composition root) — never deep in the code.
"""
from __future__ import annotations

import os
from functools import cached_property
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.app.ports import AgentRunner, Clock
from src.infra.clock import SystemClock
from src.infra.db.engine import build_engine, db_url_for_home, make_session_factory
from src.infra.db.reference_repos import (
    SqliteAgentRepository,
    SqliteCapabilityRepository,
    SqliteConfigStore,
    SqliteModelProviderRepository,
    SqliteModelRepository,
    SqliteProjectRepository,
)
from src.infra.db.agent_event_sink import SqliteAgentEventSink
from src.infra.db.chat_repository import SqliteChatRepository
from src.infra.db.secret_store import SqliteSecretStore, load_master_key
from src.infra.db.unit_of_work import SqliteUnitOfWork
from src.infra.git.workspace import GitBranchWorkspace
from src.infra.runtime.cli_runner import (
    ClaudeCodeRunner,
    GeminiRunner,
    PiAgentRunner,
)
from src.infra.reasoner.stub_reasoner import StubReasoner
from src.infra.runtime.dummy_runner import DummyAgentRunner


class AppContainer:
    """Lazy composition root: each dependency is constructed at most once per
    container instance, only when actually needed."""

    def __init__(self, orchestrator_home: Path) -> None:
        self.orchestrator_home = orchestrator_home

    @classmethod
    def from_env(cls) -> "AppContainer":
        home = Path(
            os.environ.get("ORCHESTRATOR_HOME", str(Path.home() / ".orchestrator"))
        )
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

    @cached_property
    def secret_store(self) -> SqliteSecretStore:
        # fail-closed: a missing/invalid ORCHESTRATOR_MASTER_KEY raises here
        return SqliteSecretStore(self.session_factory, load_master_key())

    # --- Stage 5: execution adapters ---
    @cached_property
    def workspace(self) -> GitBranchWorkspace:
        repo_dir = Path(
            os.environ.get(
                "PROJECT_REPO_DIR", str(self.orchestrator_home / "workspace-repo")
            )
        )
        return GitBranchWorkspace(repo_dir)

    @cached_property
    def agent_event_sink(self) -> SqliteAgentEventSink:
        return SqliteAgentEventSink(self.session_factory)

    @cached_property
    def chat_store(self) -> SqliteChatRepository:
        return SqliteChatRepository(self.session_factory)

    @cached_property
    def agent_runner(self) -> AgentRunner:
        """Selected by AGENT_MODE; the dummy IS the dry-run runtime (same
        FailureKind taxonomy as the real CLI runners)."""
        mode = os.environ.get("AGENT_MODE", "dry-run")
        if mode == "dry-run":
            return DummyAgentRunner()
        model = os.environ.get("AGENT_MODEL", "")
        if mode == "pi":
            return PiAgentRunner(
                api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
                model=model or "claude-sonnet-4-5",
            )
        if mode == "claude":
            return ClaudeCodeRunner(
                api_key=os.environ.get("ANTHROPIC_API_KEY", ""), model=model or None
            )
        if mode == "gemini":
            return GeminiRunner(
                api_key=os.environ.get("GEMINI_API_KEY", ""),
                model=model or "gemini-2.5-pro",
            )
        raise ValueError(f"Unknown AGENT_MODE: {mode!r}")

    # --- Stage 6: the planning reasoner ---
    @cached_property
    def reasoner(self) -> StubReasoner:
        # deterministic stub; the OpenAI reasoner (roadmap 2.5) swaps in here
        return StubReasoner()
