"""The catalog-resolved agent runner: dry-run default without a master key,
per-agent runtime resolution through the registry + providers catalog, the
fail-fast/TaskFailed messages, and the seed CLI's runtime binding."""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from cryptography.fernet import Fernet

from src.app.ports import TaskFailed
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.ia_model import IAModel
from src.domain.entities.model_provider import ModelProvider
from src.domain.policies.retry_policies import RetryPolicy
from src.domain.value_objects.lifecycle import FailureKind
from src.infra.cli.main import cli
from src.infra.container import AppContainer
from src.infra.db.secret_ref import SecretRef
from src.infra.db.tables import Base
from src.infra.errors import InfrastructureError
from src.infra.runtime.cli_runner import ClaudeCodeRunner, PiAgentRunner
from src.infra.runtime.dummy_runner import DryRunAgentRunner, DummyAgentRunner
from src.infra.runtime.factory import (
    CatalogAgentRunner,
    build_agent_runner,
    validate_agent_binding,
    validate_agent_runner_mode,
)

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

    return build_agent_runner(c.config_store, c.provider_repo, c.model_repo, secret_store)


def spec_with(**overrides) -> AgentSpec:
    base = dict(
        id="a1",
        name="a1",
        role="implementer",
        model_role="smart",
        instructions="",
        default_retry=RetryPolicy(),
    )
    base.update(overrides)
    return AgentSpec(**base)


def seed_anthropic(container, monkeypatch) -> None:
    """A provider row whose id maps to a pi backend, one model, and the key."""
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    ref = SecretRef.for_provider("anthropic")
    container.secret_store.put(ref, "sk-agent-test")
    container.provider_repo.add(
        ModelProvider(
            id="anthropic",
            name="anthropic",
            base_url="https://api.anthropic.com/v1/",
            api_key_ref=ref.uri,
            models=[
                IAModel(
                    id="anthropic:sonnet",
                    provider_id="anthropic",
                    name="claude-sonnet-4-5",
                )
            ],
        )
    )


def test_dry_run_is_the_default_and_never_touches_secrets(container):
    # no ORCHESTRATOR_MASTER_KEY in the env: constructing the secret store
    # would fail closed — the dry-run path must never get there
    assert isinstance(build(container), DryRunAgentRunner)


def test_invalid_mode_rejected(container):
    scope = container.config_store.ORCHESTRATOR_SCOPE
    container.config_store.set(scope, "agent_runner.mode", "banana")
    with pytest.raises(InfrastructureError) as err:
        build(container)
    assert err.value.code == "AGENT_RUNNER_CONFIG_INVALID"
    assert "dry-run" in str(err.value) and "real" in str(err.value)

    status = validate_agent_runner_mode(container.config_store)
    assert status.valid is False
    assert status.detail == str(err.value)


def test_real_mode_resolves_per_agent_through_the_catalog(container, monkeypatch):
    seed_anthropic(container, monkeypatch)
    scope = container.config_store.ORCHESTRATOR_SCOPE
    container.config_store.set(scope, "agent_runner.mode", "real")
    container.config_store.set(scope, "agent_runner.timeout_seconds", "120")

    runner = build(container)
    assert isinstance(runner, CatalogAgentRunner)

    pi = runner._runner_for(
        spec_with(runtime_type="pi", provider_id="anthropic", model_id="anthropic:sonnet")
    )
    assert isinstance(pi, PiAgentRunner)
    assert pi._api_key == "sk-agent-test"  # the catalog key, not an env var
    assert pi._model == "claude-sonnet-4-5"  # the provider model string
    assert pi._backend == "anthropic"
    assert pi._timeout == 120

    claude = runner._runner_for(
        spec_with(runtime_type="claude", provider_id="anthropic", model_id="anthropic:sonnet")
    )
    assert isinstance(claude, ClaudeCodeRunner)


def test_pi_command_disables_project_state_reuse_and_preserves_extra_flags():
    runner = PiAgentRunner(
        api_key="sk-agent-test",
        model="claude-sonnet-4-5",
        extra_flags=["--thinking", "high"],
    )

    command = runner._build_cmd("implement the task")

    assert command == [
        "pi",
        "--model",
        "claude-sonnet-4-5",
        "--no-session",
        "--no-context-files",
        "--mode",
        "json",
        "-p",
        "implement the task",
        "--thinking",
        "high",
    ]


def test_real_mode_dry_run_agent_uses_dummy_without_secrets(container):
    # NO master key in the env: a dry-run agent inside real mode must resolve
    # to the dummy without ever constructing the secret store
    scope = container.config_store.ORCHESTRATOR_SCOPE
    container.config_store.set(scope, "agent_runner.mode", "real")
    runner = build(container)
    assert isinstance(runner._runner_for(spec_with(runtime_type="dry-run")), DummyAgentRunner)


def test_broken_binding_raises_terminal_task_failed(container):
    scope = container.config_store.ORCHESTRATOR_SCOPE
    container.config_store.set(scope, "agent_runner.mode", "real")
    runner = build(container)

    spec = spec_with(runtime_type="pi")  # no provider bound
    binding = validate_agent_binding(spec, container.provider_repo, container.model_repo)
    assert binding.valid is False

    with pytest.raises(TaskFailed) as err:
        runner._runner_for(spec)
    assert err.value.kind == FailureKind.AUTH_ERROR  # terminal, no retry churn
    assert err.value.reason == binding.detail


def test_binding_validation_covers_the_wiring_failures(container, monkeypatch):
    seed_anthropic(container, monkeypatch)
    repo_args = (container.provider_repo, container.model_repo)

    assert (
        "runtime_type" in validate_agent_binding(spec_with(runtime_type="cobol"), *repo_args).detail
    )
    assert (
        "no provider_id"
        in validate_agent_binding(spec_with(runtime_type="claude"), *repo_args).detail
    )
    assert (
        "no model_id"
        in validate_agent_binding(
            spec_with(runtime_type="claude", provider_id="anthropic"), *repo_args
        ).detail
    )
    assert (
        "does not exist"
        in validate_agent_binding(
            spec_with(runtime_type="claude", provider_id="ghost", model_id="g:m"),
            *repo_args,
        ).detail
    )
    assert (
        "does not exist"
        in validate_agent_binding(
            spec_with(runtime_type="claude", provider_id="anthropic", model_id="ghost"),
            *repo_args,
        ).detail
    )

    # a provider that maps to no pi backend is fine for claude, invalid for pi
    ref = SecretRef.for_provider("acme")
    container.secret_store.put(ref, "sk-acme")
    container.provider_repo.add(
        ModelProvider(
            id="acme",
            name="Acme LLM Co",
            base_url="https://llm.acme.dev/v1",
            api_key_ref=ref.uri,
            models=[IAModel(id="acme:m1", provider_id="acme", name="m1")],
        )
    )
    ok_for_claude = validate_agent_binding(
        spec_with(runtime_type="claude", provider_id="acme", model_id="acme:m1"),
        *repo_args,
    )
    assert ok_for_claude.valid is True
    not_for_pi = validate_agent_binding(
        spec_with(runtime_type="pi", provider_id="acme", model_id="acme:m1"),
        *repo_args,
    )
    assert not_for_pi.valid is False
    assert "pi backend" in not_for_pi.detail

    cross = validate_agent_binding(
        spec_with(runtime_type="claude", provider_id="acme", model_id="anthropic:sonnet"),
        *repo_args,
    )
    assert cross.valid is False
    assert "belongs to provider" in cross.detail


def test_provider_and_model_bound_to_agent_are_delete_guarded(container, monkeypatch):
    seed_anthropic(container, monkeypatch)
    container.agent_repo.add(
        spec_with(runtime_type="claude", provider_id="anthropic", model_id="anthropic:sonnet")
    )
    from src.domain.errors.config_errors import ReferencedEntityInUseError

    with pytest.raises(ReferencedEntityInUseError):
        container.model_repo.delete("anthropic:sonnet")
    with pytest.raises(ReferencedEntityInUseError):
        container.provider_repo.delete("anthropic")


def test_seed_binds_the_demo_agent_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)

    runner = CliRunner()
    result = runner.invoke(cli, ["seed", "demo", "--stub"])
    assert result.exit_code == 0, result.output
    seeded = container.agent_repo.get("dev-agent")
    assert seeded.runtime_type == "dry-run"
    assert {capability.id for capability in seeded.capabilities} >= {
        "test_authoring",
        "implementation",
    }

    result = runner.invoke(cli, ["seed", "demo", "--provider", "openrouter", "--model", "gpt-x"])
    assert result.exit_code == 0, result.output
    agent = container.agent_repo.get("dev-agent")
    assert agent.runtime_type == "pi"  # openrouter maps to a pi backend
    assert agent.provider_id == "openrouter"
    assert agent.model_id == "openrouter:gpt-x"
    binding = validate_agent_binding(agent, container.provider_repo, container.model_repo)
    assert binding.valid is True
