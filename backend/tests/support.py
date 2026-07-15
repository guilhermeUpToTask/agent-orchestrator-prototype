"""Shared test harness: one Env interface, two builders.

make_memory_env()  — the in-memory fakes (fast unit runs).
make_sqlite_env()  — the REAL SQLite UnitOfWork/repository/outbox on a tmp db.

The parametrized `env_factory` fixture (tests/unit/orchestration/conftest.py)
runs the orchestration suite against BOTH. The sqlite run is the roadmap's
INTEGRATION TRUTH-TEST: crash-recovery, outbox-rollback and
backoff-gate-survives-crash passing on the real UoW proves the transactional
atomicity is real, not simulated by the fake.

Both envs share the same FakeClock/DummyAgentRunner/NoOpWorkspace/agent fakes —
only the persistence boundary changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from sqlalchemy import text

from src.app.ports import UnitOfWork
from src.app.testing.fakes import (
    CollectingEventSink,
    DummyAgentRunner,
    DummyBehavior,
    FakeClock,
    InMemoryAgentRepository,
    InMemoryOutbox,
    InMemoryPlanRepository,
    InMemoryUnitOfWork,
    NoOpWorkspace,
)
from src.domain.aggregates.planner_orchestrator import Plan
from src.domain.entities.agent_spec import AgentSpec
from src.domain.policies.retry_policies import RetryPolicy
from src.infra.db.engine import build_engine, make_session_factory
from src.infra.db.tables import Base
from src.infra.db.unit_of_work import SqliteUnitOfWork


def make_agent_spec(agent_id: str = "a1") -> AgentSpec:
    return AgentSpec(
        id=agent_id,
        name=agent_id,
        role="agent",
        model_role="agent",
        instructions="",
        default_retry=RetryPolicy(),
    )


class EnvBuilder(Protocol):
    def __call__(
        self,
        script: dict[str, DummyBehavior] | None = None,
        agents: list[AgentSpec] | None = None,
        default_agent_id: str = "a1",
    ) -> "Env": ...


@dataclass
class Env:
    """Everything a drive/advance test needs, backend-agnostic."""

    uow: UnitOfWork
    clock: FakeClock
    runner: DummyAgentRunner
    agents: InMemoryAgentRepository
    ws: NoOpWorkspace
    sink: CollectingEventSink
    seed: Callable[[Plan], None]
    stored: Callable[[str], Plan]
    outbox_types: Callable[[], list[str]]
    args: tuple = field(init=False)

    def __post_init__(self) -> None:
        # positional collaborators of advance_plan/drive_plan/worker_tick
        self.args = (self.uow, self.runner, self.agents, self.ws, self.sink, self.clock)


def make_memory_env(
    script: dict[str, DummyBehavior] | None = None,
    agents: list[AgentSpec] | None = None,
    default_agent_id: str = "a1",
) -> Env:
    clock = FakeClock()
    repo = InMemoryPlanRepository(clock)
    outbox = InMemoryOutbox()
    return Env(
        uow=InMemoryUnitOfWork(repo, outbox),
        clock=clock,
        runner=DummyAgentRunner(script or {}),
        agents=InMemoryAgentRepository(
            agents if agents is not None else [make_agent_spec()], default_agent_id
        ),
        ws=NoOpWorkspace(),
        sink=CollectingEventSink(),
        seed=repo.add,
        stored=repo.get,
        outbox_types=outbox.types,
    )


def make_sqlite_env(
    db_path: Path,
    script: dict[str, DummyBehavior] | None = None,
    agents: list[AgentSpec] | None = None,
    default_agent_id: str = "a1",
) -> Env:
    clock = FakeClock()
    engine = build_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    uow = SqliteUnitOfWork(make_session_factory(engine), clock)

    def seed(plan: Plan) -> None:
        if plan.project_id is not None:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT OR IGNORE INTO projects (id, name, repo_url) "
                        "VALUES (:id, :name, NULL)"
                    ),
                    {"id": plan.project_id, "name": plan.project_id},
                )
        with uow:
            uow.plans.save(plan)

    def stored(plan_id: str) -> Plan:
        with uow:
            return uow.plans.get(plan_id)

    def outbox_types() -> list[str]:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT type FROM outbox ORDER BY id"))
            return [str(r[0]) for r in rows]

    return Env(
        uow=uow,
        clock=clock,
        runner=DummyAgentRunner(script or {}),
        agents=InMemoryAgentRepository(
            agents if agents is not None else [make_agent_spec()], default_agent_id
        ),
        ws=NoOpWorkspace(),
        sink=CollectingEventSink(),
        seed=seed,
        stored=stored,
        outbox_types=outbox_types,
    )
