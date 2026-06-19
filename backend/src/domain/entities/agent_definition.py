"""
src/domain/entities/agent_definition.py — AgentDefinition entity.

An AgentDefinition is the GLOBAL configuration record for an agent: which
runtime to use, which provider/model it runs on, and what it is capable of. It
is deliberately distinct from ``AgentProps`` (the runtime registration snapshot
used for scheduling/selection): the definition is the durable config the
operator edits, while AgentProps is derived from it for the live registry.

The bridge from a definition to runtime props lives in
``src.domain.services.agent_mapping``.
"""
from __future__ import annotations

from pydantic import BaseModel

from src.domain.value_objects.capability import CapabilityTag


class AgentDefinition(BaseModel):
    id: str
    name: str
    capabilities: tuple[CapabilityTag, ...] = ()
    runtime_type: str                       # pi | claude | gemini
    provider_id: str                        # FK -> ModelProvider.id
    model_id: str
    state_version: int = 0
