"""
src/domain/services/agent_mapping.py — AgentDefinition → AgentProps bridge.

The global AgentDefinition is the durable config an operator edits; AgentProps is
the runtime registration snapshot the scheduler and selectors consume. This pure
function derives the latter from the former, folding the provider's model/base_url
into ``runtime_config`` so the existing runtime factory keeps working unchanged.
"""
from __future__ import annotations

from typing import Any

from src.domain.entities.agent import AgentProps
from src.domain.entities.agent_definition import AgentDefinition
from src.domain.entities.model_provider import ModelProvider


def agent_definition_to_props(
    definition: AgentDefinition,
    provider: ModelProvider,
) -> AgentProps:
    """
    Build the runtime ``AgentProps`` for a definition.

    Raises ValueError if the provider does not match the definition's
    ``provider_id`` — callers must resolve the correct provider first.
    """
    if provider.id != definition.provider_id:
        raise ValueError(
            f"Provider '{provider.id}' does not match agent definition "
            f"provider_id '{definition.provider_id}'"
        )

    runtime_config: dict[str, Any] = {"model": definition.model_id}
    if provider.base_url:
        runtime_config["base_url"] = provider.base_url

    return AgentProps(
        agent_id=definition.id,
        name=definition.name,
        capabilities=list(definition.capabilities),
        runtime_type=definition.runtime_type,
        runtime_config=runtime_config,
    )
