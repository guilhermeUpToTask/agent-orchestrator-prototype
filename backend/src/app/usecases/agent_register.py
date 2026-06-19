"""
src/app/usecases/agent_register.py — Register an agent use case.
"""
from __future__ import annotations

from dataclasses import dataclass

from typing import Optional

from src.domain import AgentProps, CapabilityRegistryPort, UnknownCapabilityError
from src.domain.repositories import AgentRegistryPort


@dataclass(frozen=True)
class AgentRegisterResult:
    agent_id: str
    active: bool
    runtime_type: str


class AgentRegisterUseCase:
    """
    Operator action: add or update an agent entry in the registry.

    Accepts a fully constructed AgentProps so the use case stays
    free of JSON-parsing or string-splitting logic — those belong in the CLI.
    Capability tags are validated against the CapabilityRegistry: an unknown
    tag is rejected (operator error) rather than silently producing an agent
    that matches no task.
    """

    def __init__(
        self,
        agent_registry: AgentRegistryPort,
        capability_registry: Optional[CapabilityRegistryPort] = None,
    ) -> None:
        self._registry = agent_registry
        self._capabilities = capability_registry

    def execute(self, agent: AgentProps) -> AgentRegisterResult:
        if self._capabilities is not None:
            for tag in agent.capabilities:
                if not self._capabilities.exists(tag):
                    raise UnknownCapabilityError(tag, self._capabilities.list_tags())
        self._registry.register(agent)
        return AgentRegisterResult(
            agent_id=agent.agent_id,
            active=agent.active,
            runtime_type=agent.runtime_type,
        )
