"""Reference-data repositories on real SQLite: CRUD, the delete-guard /
cascade-down/guard-up integrity rules, and the default-agent marker."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from src.app.testing.fakes import FakeClock
from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.capability import Capability
from src.domain.entities.goal import Goal
from src.domain.entities.ia_model import IAModel
from src.domain.entities.model_provider import ModelProvider
from src.domain.entities.project_definition import ProjectDefinition
from src.domain.entities.task import Task
from src.domain.errors.agent_errors import AgentNotFoundError, NoDefaultAgentError
from src.domain.errors.config_errors import (
    CapabilityNotFoundError,
    EntityAlreadyExistsError,
    ModelProviderNotFoundError,
    ReferencedEntityInUseError,
)
from src.domain.policies.retry_policies import RetryPolicy
from src.infra.db.engine import build_engine, make_session_factory
from src.infra.db.reference_repos import (
    SqliteAgentRepository,
    SqliteCapabilityRepository,
    SqliteConfigStore,
    SqliteModelProviderRepository,
    SqliteModelRepository,
    SqliteProjectRepository,
)
from src.infra.db.tables import Base
from src.infra.db.unit_of_work import SqliteUnitOfWork

pytestmark = pytest.mark.integration


@pytest.fixture
def sf(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def cap(cid="c1"):
    return Capability(id=cid, name=cid, description="d", tools=["grep"])


def agent(aid="a1", caps=()):
    return AgentSpec(
        id=aid,
        name=aid,
        role="agent",
        model_role="smart",
        instructions="do work",
        capabilities=list(caps),
        default_retry=RetryPolicy(),
    )


# ---- capabilities ----
def test_capability_crud_roundtrip(sf):
    repo = SqliteCapabilityRepository(sf)
    repo.add(cap())
    assert repo.get("c1").tools == ["grep"]
    repo.update(Capability(id="c1", name="renamed", description="d2", tools=[]))
    assert repo.get("c1").name == "renamed"
    assert [c.id for c in repo.list()] == ["c1"]
    repo.delete("c1")
    with pytest.raises(CapabilityNotFoundError):
        repo.get("c1")


def test_capability_duplicate_add_rejected(sf):
    repo = SqliteCapabilityRepository(sf)
    repo.add(cap())
    with pytest.raises(EntityAlreadyExistsError):
        repo.add(cap())


def test_capability_delete_guarded_by_agent_reference(sf):
    caps, agents = SqliteCapabilityRepository(sf), SqliteAgentRepository(sf)
    caps.add(cap())
    agents.add(agent(caps=[cap()]))
    with pytest.raises(ReferencedEntityInUseError):
        caps.delete("c1")


def test_capability_delete_guarded_by_active_plan(sf):
    caps = SqliteCapabilityRepository(sf)
    caps.add(cap())
    uow = SqliteUnitOfWork(sf, FakeClock())
    plan = Plan(
        id="p1",
        brief="b",
        phase=PlanPhase.RUNNING,
        goals=[
            Goal(
                id="g1",
                name="g",
                position=0,
                description="",
                tasks=[
                    Task(
                        id="t1",
                        name="t",
                        position=0,
                        description="",
                        required_capabilities=["c1"],
                    )
                ],
            )
        ],
    )
    with uow:
        uow.plans.save(plan)
    with pytest.raises(ReferencedEntityInUseError):
        caps.delete("c1")


# ---- agents ----
def test_agent_crud_hydrates_capabilities(sf):
    caps, agents = SqliteCapabilityRepository(sf), SqliteAgentRepository(sf)
    caps.add(cap())
    agents.add(agent(caps=[cap()]))
    loaded = agents.get("a1")
    assert [c.id for c in loaded.capabilities] == ["c1"]
    assert loaded.default_retry.max_attempts == 3

    loaded.capabilities = []
    agents.update(loaded)
    assert agents.get("a1").capabilities == []


def test_agent_add_with_unknown_capability_rejected(sf):
    agents = SqliteAgentRepository(sf)
    with pytest.raises(CapabilityNotFoundError):
        agents.add(agent(caps=[cap("ghost")]))


def test_agent_delete_guarded_by_active_plan_binding(sf):
    agents = SqliteAgentRepository(sf)
    agents.add(agent())
    uow = SqliteUnitOfWork(sf, FakeClock())
    plan = Plan(
        id="p1",
        brief="b",
        phase=PlanPhase.RUNNING,
        goals=[
            Goal(
                id="g1",
                name="g",
                position=0,
                description="",
                tasks=[
                    Task(id="t1", name="t", position=0, description="", agent_id="a1")
                ],
            )
        ],
    )
    with uow:
        uow.plans.save(plan)
    with pytest.raises(ReferencedEntityInUseError):
        agents.delete("a1")


def test_agent_delete_allowed_when_plan_terminal(sf):
    agents = SqliteAgentRepository(sf)
    agents.add(agent())
    uow = SqliteUnitOfWork(sf, FakeClock())
    plan = Plan(id="p1", brief="b", phase=PlanPhase.DONE)
    with uow:
        uow.plans.save(plan)
    agents.delete("a1")
    with pytest.raises(AgentNotFoundError):
        agents.get("a1")


def test_default_agent_marker(sf):
    agents = SqliteAgentRepository(sf)
    with pytest.raises(NoDefaultAgentError):
        agents.default_agent_id()
    agents.add(agent("a1"))
    agents.add(agent("a2"))
    agents.set_default("a1")
    assert agents.default_agent_id() == "a1"
    agents.set_default("a2")  # exclusive: moves, not accumulates
    assert agents.default_agent_id() == "a2"


# ---- providers & models ----
def test_provider_hydrates_and_reconciles_models(sf):
    providers = SqliteModelProviderRepository(sf)
    p = ModelProvider(
        id="prov1",
        name="P",
        base_url="https://api",
        api_key_ref="secret://provider/prov1",
        models=[IAModel(id="m1", provider_id="prov1", name="model-one")],
    )
    providers.add(p)
    loaded = providers.get("prov1")
    assert [m.id for m in loaded.models] == ["m1"]

    loaded.add_model(IAModel(id="m2", provider_id="prov1", name="model-two"))
    loaded.delete_model(loaded.get_model("m1"))
    providers.update(loaded)
    assert [m.id for m in providers.get("prov1").models] == ["m2"]


def test_provider_delete_cascades_to_models(sf):
    providers, models = SqliteModelProviderRepository(sf), SqliteModelRepository(sf)
    providers.add(
        ModelProvider(
            id="prov1",
            name="P",
            base_url="u",
            api_key_ref="secret://provider/prov1",
            models=[IAModel(id="m1", provider_id="prov1", name="n")],
        )
    )
    providers.delete("prov1")
    with pytest.raises(ModelProviderNotFoundError):
        providers.get("prov1")
    assert models.list() == []  # cascade-down


def test_provider_delete_guarded_up_when_model_in_use(sf):
    providers = SqliteModelProviderRepository(sf)
    config = SqliteConfigStore(sf)
    providers.add(
        ModelProvider(
            id="prov1",
            name="P",
            base_url="u",
            api_key_ref="secret://provider/prov1",
            models=[IAModel(id="m1", provider_id="prov1", name="n")],
        )
    )
    config.set("orchestrator", "model_role.smart", "m1")  # tier mapping uses m1
    with pytest.raises(ReferencedEntityInUseError):
        providers.delete("prov1")
    config.delete("orchestrator", "model_role.smart")
    providers.delete("prov1")  # unguarded once the reference is gone


def test_model_repo_list_by_provider(sf):
    providers, models = SqliteModelProviderRepository(sf), SqliteModelRepository(sf)
    providers.add(
        ModelProvider(
            id="prov1", name="P", base_url="u",
            api_key_ref="secret://provider/prov1", models=[],
        )
    )
    models.add(IAModel(id="m1", provider_id="prov1", name="n1"))
    models.add(IAModel(id="m2", provider_id="prov1", name="n2"))
    assert [m.id for m in models.list_by_provider("prov1")] == ["m1", "m2"]


# ---- projects & config ----
def test_project_crud(sf):
    projects = SqliteProjectRepository(sf)
    projects.add(ProjectDefinition(id="pr1", name="demo", repo_url=None))
    assert projects.get("pr1").name == "demo"
    projects.update(ProjectDefinition(id="pr1", name="demo2", repo_url="file:///r"))
    assert projects.get("pr1").repo_url == "file:///r"
    projects.delete("pr1")
    assert projects.list() == []


def test_config_two_tier_scoping(sf):
    config = SqliteConfigStore(sf)
    config.set("orchestrator", "poll_seconds", "2")
    config.set("pr1", "framework", "fastapi")
    assert config.get("orchestrator", "poll_seconds") == "2"
    assert config.get("pr1", "poll_seconds") is None  # scopes are isolated
    assert config.all("pr1") == {"framework": "fastapi"}
    config.set("pr1", "framework", "flask")  # upsert
    assert config.get("pr1", "framework") == "flask"


def test_foreign_keys_are_enforced(sf):
    """The engine PRAGMA foreign_keys=ON must actually hold for the guards to work."""
    with sf() as s:
        assert s.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
