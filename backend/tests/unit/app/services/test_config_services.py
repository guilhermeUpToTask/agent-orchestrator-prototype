"""Unit tests for ProjectService and RegistryService (Phase 1, in-memory fakes)."""
from __future__ import annotations

import pytest
from pydantic import SecretStr

from src.app.errors import ResourceNotFoundException, ValidationException
from src.app.services.project_service import ProjectService, slugify
from src.app.services.registry_service import RegistryService
from src.domain.entities.agent_definition import AgentDefinition
from src.domain.entities.model_provider import ModelProvider
from src.domain.entities.project import Project
from src.domain.errors import ConflictException, ReferentialException
from src.domain.value_objects.config import ProviderKind, SecretRef


# ---------------------------------------------------------------------------
# In-memory fakes (no I/O) — satisfy the ports for Phase 1 service tests.
# ---------------------------------------------------------------------------

class FakeConfigStore:
    def __init__(self) -> None:
        self.projects: dict[str, Project] = {}
        self.agents: dict[str, AgentDefinition] = {}
        self.providers: dict[str, ModelProvider] = {}

    # projects
    def create_project(self, p: Project) -> Project:
        self.projects[p.id] = p
        return p

    def get_project(self, pid: str) -> Project | None:
        return self.projects.get(pid)

    def list_projects(self) -> tuple[Project, ...]:
        return tuple(self.projects.values())

    def update_project(self, p: Project) -> Project:
        cur = self.projects.get(p.id)
        if cur and cur.state_version != p.state_version:
            raise ConflictException("stale", expected_version=p.state_version,
                                    actual_version=cur.state_version)
        self.projects[p.id] = p
        return p

    def delete_project(self, pid: str, *, cascade: bool = False) -> None:
        self.projects.pop(pid, None)

    # agents
    def upsert_agent(self, a: AgentDefinition) -> AgentDefinition:
        self.agents[a.id] = a
        return a

    def get_agent(self, aid: str) -> AgentDefinition | None:
        return self.agents.get(aid)

    def list_agents(self) -> tuple[AgentDefinition, ...]:
        return tuple(self.agents.values())

    def delete_agent(self, aid: str) -> None:
        self.agents.pop(aid, None)

    # providers
    def upsert_provider(self, p: ModelProvider) -> ModelProvider:
        self.providers[p.id] = p
        return p

    def get_provider(self, pid: str) -> ModelProvider | None:
        return self.providers.get(pid)

    def list_providers(self) -> tuple[ModelProvider, ...]:
        return tuple(self.providers.values())

    def delete_provider(self, pid: str) -> None:
        if any(a.provider_id == pid for a in self.agents.values()):
            raise ReferentialException(f"provider {pid} still referenced")
        self.providers.pop(pid, None)


class FakeSecretStore:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def put(self, ref: SecretRef, plaintext: str) -> None:
        self.data[ref.uri] = plaintext

    def resolve(self, ref: SecretRef) -> SecretStr:
        if ref.uri not in self.data:
            raise ResourceNotFoundException("missing secret")
        return SecretStr(self.data[ref.uri])

    def exists(self, ref: SecretRef) -> bool:
        return ref.uri in self.data

    def delete(self, ref: SecretRef) -> None:
        self.data.pop(ref.uri, None)


class FakeActiveProject:
    def __init__(self) -> None:
        self.active: dict[str, str] = {}

    def get_active(self, session_id: str) -> str | None:
        return self.active.get(session_id)

    def set_active(self, session_id: str, project_id: str) -> None:
        self.active[session_id] = project_id


class FakeAgentRegistry:
    def __init__(self) -> None:
        self.agents: dict = {}

    def register(self, agent) -> None:
        self.agents[agent.agent_id] = agent

    def deregister(self, agent_id: str) -> None:
        self.agents.pop(agent_id, None)

    def list_agents(self):
        return list(self.agents.values())

    def get(self, agent_id: str):
        return self.agents.get(agent_id)

    def heartbeat(self, agent_id: str) -> bool:
        return agent_id in self.agents


@pytest.fixture
def stores() -> tuple[FakeConfigStore, FakeSecretStore, FakeActiveProject]:
    return FakeConfigStore(), FakeSecretStore(), FakeActiveProject()


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------

def test_slugify() -> None:
    assert slugify("My Cool Project") == "my-cool-project"
    with pytest.raises(ValidationException):
        slugify("   ")


# ---------------------------------------------------------------------------
# ProjectService
# ---------------------------------------------------------------------------

class TestProjectService:
    def test_create_derives_id_and_stores_github_token(self, stores) -> None:
        config, secrets, active = stores
        svc = ProjectService(config, secrets, active)
        proj = svc.create_project(
            name="Web App", repo_url="git@x:y.git", github_token="ghp_secret"
        )
        assert proj.id == "web-app"
        assert proj.github_secret_ref == SecretRef.for_project_github("web-app")
        # secret stored as ciphertext-equivalent, resolvable, not on the entity
        assert secrets.resolve(proj.github_secret_ref).get_secret_value() == "ghp_secret"

    def test_create_without_token_has_no_secret_ref(self, stores) -> None:
        config, secrets, active = stores
        svc = ProjectService(config, secrets, active)
        proj = svc.create_project(name="No Token", repo_url="r")
        assert proj.github_secret_ref is None

    def test_get_missing_raises_404_code(self, stores) -> None:
        config, secrets, active = stores
        svc = ProjectService(config, secrets, active)
        with pytest.raises(ResourceNotFoundException) as ei:
            svc.get_project("nope")
        assert ei.value.code == "PROJECT_NOT_FOUND"

    def test_activate_sets_active_and_validates(self, stores) -> None:
        config, secrets, active = stores
        svc = ProjectService(config, secrets, active)
        svc.create_project(name="P", repo_url="r")
        svc.activate("session-1", "p")
        assert active.get_active("session-1") == "p"
        assert svc.get_active("session-1").id == "p"

    def test_activate_missing_raises(self, stores) -> None:
        config, secrets, active = stores
        svc = ProjectService(config, secrets, active)
        with pytest.raises(ResourceNotFoundException):
            svc.activate("s", "ghost")


# ---------------------------------------------------------------------------
# RegistryService
# ---------------------------------------------------------------------------

class TestRegistryService:
    def _svc(self, stores) -> RegistryService:
        config, secrets, _ = stores
        return RegistryService(config, secrets)

    def test_register_provider_stores_key(self, stores) -> None:
        _, secrets, _ = stores
        svc = self._svc(stores)
        prov = svc.register_provider(
            provider_id="anthropic", kind=ProviderKind.ANTHROPIC, api_key="sk-123"
        )
        assert prov.secret_ref == SecretRef.for_provider("anthropic")
        assert secrets.resolve(prov.secret_ref).get_secret_value() == "sk-123"

    def test_add_model_and_register_agent(self, stores) -> None:
        svc = self._svc(stores)
        svc.register_provider(
            provider_id="anthropic", kind=ProviderKind.ANTHROPIC, api_key="k"
        )
        svc.add_model(provider_id="anthropic", model_id="claude-opus-4-8")
        agent = svc.register_agent(
            agent_id="a1", name="Worker", runtime_type="claude",
            provider_id="anthropic", model_id="claude-opus-4-8",
            capabilities=("code:backend",),
        )
        assert agent.id == "a1"

    def test_register_agent_writes_through_to_registry(self, stores) -> None:
        config, secrets, _ = stores
        registry = FakeAgentRegistry()
        svc = RegistryService(config, secrets, registry)
        svc.register_provider(
            provider_id="anthropic", kind=ProviderKind.ANTHROPIC, api_key="k",
            base_url="https://proxy",
        )
        svc.add_model(provider_id="anthropic", model_id="claude-opus-4-8")
        svc.register_agent(
            agent_id="a1", name="Worker", runtime_type="claude",
            provider_id="anthropic", model_id="claude-opus-4-8",
            capabilities=("code:backend",),
        )
        # Derived AgentProps is now schedulable from the runtime registry.
        props = registry.get("a1")
        assert props is not None
        assert props.runtime_type == "claude"
        assert props.runtime_config["model"] == "claude-opus-4-8"
        assert props.runtime_config["base_url"] == "https://proxy"
        # And deregistration removes it.
        svc.delete_agent("a1")
        assert registry.get("a1") is None

    def test_register_agent_unknown_model_raises(self, stores) -> None:
        svc = self._svc(stores)
        svc.register_provider(
            provider_id="anthropic", kind=ProviderKind.ANTHROPIC, api_key="k"
        )
        with pytest.raises(ValidationException) as ei:
            svc.register_agent(
                agent_id="a1", name="W", runtime_type="claude",
                provider_id="anthropic", model_id="ghost-model",
            )
        assert ei.value.code == "MODEL_NOT_REGISTERED"

    def test_register_agent_unknown_provider_raises(self, stores) -> None:
        svc = self._svc(stores)
        with pytest.raises(ResourceNotFoundException):
            svc.register_agent(
                agent_id="a1", name="W", runtime_type="claude",
                provider_id="ghost", model_id="m",
            )

    def test_delete_referenced_provider_raises(self, stores) -> None:
        svc = self._svc(stores)
        svc.register_provider(
            provider_id="anthropic", kind=ProviderKind.ANTHROPIC, api_key="k"
        )
        svc.add_model(provider_id="anthropic", model_id="m")
        svc.register_agent(
            agent_id="a1", name="W", runtime_type="claude",
            provider_id="anthropic", model_id="m",
        )
        with pytest.raises(ReferentialException):
            svc.delete_provider("anthropic")
