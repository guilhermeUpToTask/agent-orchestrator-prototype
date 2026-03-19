"""
src/app/usecases/agent_register.py — Register an agent use case.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.domain import AgentProps
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
    """

    def __init__(self, agent_registry: AgentRegistryPort) -> None:
        self._registry = agent_registry

    def execute(self, agent: AgentProps) -> AgentRegisterResult:
        self._registry.register(agent)
        return AgentRegisterResult(
            agent_id=agent.agent_id,
            active=agent.active,
            runtime_type=agent.runtime_type,
        )
