from __future__ import annotations
from src.domain.entities.agent_spec import AgentSpec


def matching_agent_id(
    required_capabilities: list[str],
    agents: list[AgentSpec],
) -> str | None:
    """Return the first configured agent covering every required capability."""
    required = set(required_capabilities)
    for agent in agents:
        agent_caps = {c.id for c in agent.capabilities}
        if required.issubset(agent_caps):
            return agent.id
    return None


def match_agent(
    required_capabilities: list[str],
    agents: list[AgentSpec],
    default_agent_id: str,
) -> tuple[str, bool]:
    """Pure function: first agent whose capabilities cover the requirements.
    Returns (agent_id, used_default). Free function (not a Task method) so it is
    trivially testable and decoupled from the entity."""
    agent_id = matching_agent_id(required_capabilities, agents)
    if agent_id is not None:
        return agent_id, False
    return default_agent_id, True
