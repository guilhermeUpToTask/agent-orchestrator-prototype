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
from sqlalchemy.engine import Engine

from praxis_orchestrator.app.execution_services import ExecutionServices
from praxis_orchestrator.app.ports import UnitOfWork
from praxis_orchestrator.app.testing.fakes import (
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
from praxis_orchestrator.domain.aggregates.planner_orchestrator import Plan
from praxis_orchestrator.domain.entities.agent_spec import AgentSpec
from praxis_orchestrator.domain.policies.retry_policies import RetryPolicy
from praxis_orchestrator.infra.db.engine import build_engine, make_session_factory
from praxis_orchestrator.infra.db.tables import Base
from praxis_orchestrator.infra.db.unit_of_work import SqliteUnitOfWork


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
    services: ExecutionServices = field(init=False)
    args: tuple = field(init=False)

    def __post_init__(self) -> None:
        # P8.7 task 4: the drivers take ONE collaborator bundle, so the five
        # adapters a test builds travel together here too — a test that forgets
        # one is exactly the production bug ExecutionServices exists to prevent.
        self.services = ExecutionServices(
            runner=self.runner,
            agents=self.agents,
            workspace=self.ws,
            event_sink=self.sink,
            clock=self.clock,
        )
        # positional arguments of advance_plan/drive_plan/worker_tick, minus the
        # per-call identity (plan id, goal id, worker id) each caller supplies.
        self.args = (self.uow, self.services)


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


# Plan-scoped tables all declare ON DELETE CASCADE to `plans` (migration 0015),
# so a test writing outbox rows or agent telemetry must have a plan to hang them
# on. Before that constraint existed these rows could reference nothing; now the
# foreign key rejects them, which is the point.
_BARE_PLAN_SQL = text(
    """
    INSERT OR IGNORE INTO plans
        (id, project_id, version, status, phase, iteration, data,
         retry_not_before, paused, pause_requested, created_at, updated_at)
    VALUES (:id, NULL, 1, 'running', 'RUNNING', 0, '{}', NULL, 0, 0, :now, :now)
    """
)


def seed_plan_row(engine: Engine, *plan_ids: str) -> None:
    """Insert minimal `plans` rows so plan-scoped inserts satisfy their FK.

    Deliberately not a real aggregate: these tests exercise the outbox relay and
    the observation store, which care only that the plan id resolves.
    """
    now = "2026-07-27T00:00:00+00:00"
    with engine.begin() as connection:
        for plan_id in plan_ids:
            connection.execute(_BARE_PLAN_SQL, {"id": plan_id, "now": now})


@dataclass
class PromotionEnv:
    """Just a UnitOfWork — the promotion ledger needs no plan, runner or clock."""

    uow: UnitOfWork


def build_promotion_env(backend: str, tmp_path: Path) -> PromotionEnv:
    if backend == "fakes":
        return PromotionEnv(
            uow=InMemoryUnitOfWork(InMemoryPlanRepository(), InMemoryOutbox())
        )
    engine = build_engine(f"sqlite:///{tmp_path / 'promotions.db'}")
    Base.metadata.create_all(engine)
    # The FK to `plans` is enforced, so the row this test writes needs a parent.
    # `plans` has several NOT NULL columns with no server-side default
    # (phase, iteration, data, created_at, updated_at) — see PlanTable in
    # praxis_orchestrator/infra/db/tables.py — so a raw INSERT must supply all of them.
    with engine.begin() as connection:
        connection.execute(_BARE_PLAN_SQL, {"id": "p1", "now": "2026-07-28T00:00:00+00:00"})
    return PromotionEnv(uow=SqliteUnitOfWork(make_session_factory(engine), FakeClock()))
