"""Unit tests for AgentDefinition -> AgentProps mapping (Phase 1)."""
from __future__ import annotations

import pytest

from src.domain.entities.agent_definition import AgentDefinition
from src.domain.entities.model_provider import ModelProvider
from src.domain.services.agent_mapping import agent_definition_to_props
from src.domain.value_objects.config import ProviderKind, SecretRef


def _provider(provider_id: str = "anthropic", base_url: str | None = None) -> ModelProvider:
    return ModelProvider(
        id=provider_id,
        kind=ProviderKind.ANTHROPIC,
        secret_ref=SecretRef.for_provider(provider_id),
        base_url=base_url,
    )


def _definition(provider_id: str = "anthropic") -> AgentDefinition:
    return AgentDefinition(
        id="agent-1",
        name="Worker",
        capabilities=("code:backend",),
        runtime_type="claude",
        provider_id=provider_id,
        model_id="claude-opus-4-8",
    )


def test_maps_core_fields() -> None:
    props = agent_definition_to_props(_definition(), _provider())
    assert props.agent_id == "agent-1"
    assert props.name == "Worker"
    assert props.runtime_type == "claude"
    assert props.runtime_config["model"] == "claude-opus-4-8"
    assert "code:backend" in props.capabilities


def test_base_url_folded_into_runtime_config() -> None:
    props = agent_definition_to_props(
        _definition(), _provider(base_url="https://proxy.local")
    )
    assert props.runtime_config["base_url"] == "https://proxy.local"


def test_no_base_url_key_when_absent() -> None:
    props = agent_definition_to_props(_definition(), _provider())
    assert "base_url" not in props.runtime_config


def test_provider_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        agent_definition_to_props(_definition("anthropic"), _provider("openai"))
