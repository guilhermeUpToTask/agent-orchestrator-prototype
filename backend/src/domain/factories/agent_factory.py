"""Factory for AgentSpec — same create/reconstruct split. Demonstrates the
pattern for a reference-data entity (vs. the Plan aggregate)."""

from __future__ import annotations

from typing import Any

from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.capability import Capability
from src.domain.factories.identity import new_id
from src.domain.policies.retry_policies import RetryPolicy


class AgentFactory:
    @staticmethod
    def create(
        name: str,
        role: str,
        model_role: str,
        instructions: str,
        capabilities: list[Capability] | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> AgentSpec:
        return AgentSpec(
            id=new_id(),
            name=name,
            role=role,
            model_role=model_role,
            instructions=instructions,
            capabilities=capabilities or [],
            default_retry=retry_policy or RetryPolicy(),
        )

    @staticmethod
    def reconstruct(data: dict[str, Any]) -> AgentSpec:
        return AgentSpec.model_validate(data)
