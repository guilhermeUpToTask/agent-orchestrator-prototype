"""The catalog-resolved reasoner factory + the seed CLI: stub default, llm
fail-fast messages, full resolution on a seeded database, and the rule that
stub mode never touches the secret store (dry-run needs no master key)."""
from __future__ import annotations

import pytest
from click.testing import CliRunner
from cryptography.fernet import Fernet

from src.infra.cli.main import cli
from src.infra.container import AppContainer
from src.infra.db.tables import Base
from src.infra.errors import InfrastructureError
from src.infra.reasoner.factory import build_reasoner, validate_reasoner_config
from src.infra.reasoner.openai_reasoner import OpenAIReasoner
from src.infra.reasoner.stub_reasoner import StubReasoner

pytestmark = pytest.mark.integration


@pytest.fixture
def container(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
    monkeypatch.delenv("ORCHESTRATOR_MASTER_KEY", raising=False)
    c = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(c.engine)
    return c


def build(c):
    def secret_store():
        return c.secret_store

    return build_reasoner(
        c.config_store, c.provider_repo, c.model_repo, secret_store, c.capability_repo
    )


def test_stub_is_the_default_and_never_touches_secrets(container):
    # no ORCHESTRATOR_MASTER_KEY in the env: constructing the secret store
    # would fail closed — the stub path must never get there
    assert isinstance(build(container), StubReasoner)


def test_llm_mode_fails_fast_with_actionable_messages(container):
    scope = container.config_store.ORCHESTRATOR_SCOPE
    container.config_store.set(scope, "reasoner.mode", "llm")

    with pytest.raises(InfrastructureError) as err:
        build(container)
    assert err.value.code == "REASONER_CONFIG_INVALID"
    assert "reasoner.provider_id" in str(err.value)

    container.config_store.set(scope, "reasoner.provider_id", "ghost")
    container.config_store.set(scope, "reasoner.model_id", "ghost:m")
    with pytest.raises(InfrastructureError) as err:
        build(container)
    assert "does not exist" in str(err.value)


def test_invalid_mode_rejected(container):
    scope = container.config_store.ORCHESTRATOR_SCOPE
    container.config_store.set(scope, "reasoner.mode", "banana")
    with pytest.raises(InfrastructureError) as err:
        build(container)
    assert "stub" in str(err.value) and "llm" in str(err.value)


def test_validate_reasoner_config_never_touches_secrets(container):
    # same no-master-key container as the stub test: the validator must be
    # callable without ever constructing the secret store
    status = validate_reasoner_config(
        container.config_store, container.provider_repo, container.model_repo
    )
    assert status.mode == "stub"
    assert status.valid is True
    assert status.detail is None


def test_validate_reasoner_config_matches_build_reasoner_message(container):
    scope = container.config_store.ORCHESTRATOR_SCOPE
    container.config_store.set(scope, "reasoner.mode", "llm")

    status = validate_reasoner_config(
        container.config_store, container.provider_repo, container.model_repo
    )
    assert status.valid is False

    with pytest.raises(InfrastructureError) as err:
        build(container)
    assert str(err.value) == status.detail


def test_seed_stub_then_llm_resolves_openai_reasoner(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)

    runner = CliRunner()
    # stub seed: capabilities + agent + stub mode, twice (idempotent)
    for _ in range(2):
        result = runner.invoke(cli, ["seed", "demo", "--stub"])
        assert result.exit_code == 0, result.output
    assert {c.id for c in container.capability_repo.list()} == {
        "backend", "frontend", "testing",
    }
    assert container.agent_repo.default_agent_id() == "dev-agent"
    assert isinstance(build(container), StubReasoner)

    # llm seed: provider + model + secret + config, twice (idempotent)
    for _ in range(2):
        result = runner.invoke(
            cli,
            ["seed", "demo", "--provider", "openrouter", "--model", "gpt-x"],
        )
        assert result.exit_code == 0, result.output

    provider = container.provider_repo.get("openrouter")
    assert provider.base_url == "https://openrouter.ai/api/v1"
    assert provider.api_key_ref == "secret://provider/openrouter"
    assert "sk-test-123" not in provider.api_key_ref

    reasoner = build(container)
    assert isinstance(reasoner, OpenAIReasoner)
    assert reasoner._client.model == "gpt-x"  # the provider model string


def test_seed_llm_requires_key_in_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)

    result = CliRunner().invoke(
        cli, ["seed", "demo", "--provider", "openrouter", "--model", "m"]
    )
    assert result.exit_code != 0
    assert "OPENROUTER_API_KEY" in result.output
