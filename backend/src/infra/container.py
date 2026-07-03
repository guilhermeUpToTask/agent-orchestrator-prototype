"""AppContainer — the composition root (rebuilt during the integration).

TRANSPLANT NOTE: the old container wired the pre-refactor domain (task manager /
goal orchestrator / reconciler daemons) and was emptied with them. It grows back
stage by stage as the real adapters land behind the new ports:

  Stage 3 — engine/session factory, SystemClock, SqliteUnitOfWork  [done]
  Stage 4 — reference-data repos (agents/capabilities/providers/models), secrets
  Stage 5 — workspace + agent-runner adapters, agent-event sink
  Stage 6 — reasoner, PlanDispatcher, worker wiring
  Stage 7 — API dependency surface (SettingsService replaces the env read here)

Environment is read ONLY here (the composition root) — never deep in the code.
"""
from __future__ import annotations

import os
from functools import cached_property
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.app.ports import Clock
from src.infra.clock import SystemClock
from src.infra.db.engine import build_engine, db_url_for_home, make_session_factory
from src.infra.db.unit_of_work import SqliteUnitOfWork


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
