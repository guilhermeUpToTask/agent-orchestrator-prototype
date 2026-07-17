"""Resolve execution roles through the existing AgentSpec registry."""

from __future__ import annotations

from enum import Enum

from src.domain.entities.agent_spec import AgentSpec
from src.domain.repositories.agent_repo import AgentRepository
from src.domain.services.capability_matching import matching_agent_id


class RunRole(str, Enum):
    TEST_AUTHOR = "test_author"
    IMPLEMENTER = "implementer"
    VERIFIER = "verifier"


_ROLE_CAPABILITY = {
    RunRole.TEST_AUTHOR: "test_authoring",
    RunRole.IMPLEMENTER: "implementation",
    RunRole.VERIFIER: "verification",
}


def resolve_role_agent(
    role: RunRole,
    required_capabilities: list[str],
    agents: AgentRepository,
) -> AgentSpec:
    """Use the configured registry; a role capability is mandatory, never defaulted."""
    required = [_ROLE_CAPABILITY[role], *required_capabilities]
    catalog = agents.list()
    agent_id = matching_agent_id(required, catalog)
    if agent_id is None:
        raise ValueError(f"no configured agent covers {role.value}: {sorted(set(required))}")
    return agents.get(agent_id)


def resolve_task_role_agents(
    required_capabilities: list[str],
    agents: AgentRepository,
) -> dict[str, str]:
    """Resolve the mandatory TDD roles from the live user-managed registry."""
    return {
        RunRole.TEST_AUTHOR.value: resolve_role_agent(
            RunRole.TEST_AUTHOR, required_capabilities, agents
        ).id,
        RunRole.IMPLEMENTER.value: resolve_role_agent(
            RunRole.IMPLEMENTER, required_capabilities, agents
        ).id,
    }
