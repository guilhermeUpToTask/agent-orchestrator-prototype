"""Resolve execution roles through the existing AgentSpec registry.

Two rules make a role binding trustworthy, and the second was learned the
expensive way (P8.4 demo run, 2026-08-09):

1. **A role asks only for its OWN capability**, plus the task's domain
   capabilities. A TDD task declares `test_authoring` AND `implementation`
   because it has both stages — that is a property of the TASK, not a demand on
   every agent that touches it. Unioning the two lists made resolving
   IMPLEMENTER require an agent that could also author tests, and the only
   agents that qualify are precisely the ones whose instructions say "Do NOT
   implement the feature". The live run bound both roles to the test author and
   the GREEN stage could never succeed.

2. **A declared role is never contradicted.** An agent that calls itself a
   `test_author` is not an implementer of last resort. Binding one anyway fails
   as an agent-quality problem three layers away from the cause, which is what
   made the original defect cost a whole cycle to find.
"""

from __future__ import annotations

from enum import Enum

from agent_orchestrator.domain.entities.agent_spec import AgentSpec
from agent_orchestrator.domain.errors.agent_errors import RoleUnsatisfiableError
from agent_orchestrator.domain.repositories.agent_repo import AgentRepository
from agent_orchestrator.domain.services.capability_matching import matching_agent_id


class RunRole(str, Enum):
    TEST_AUTHOR = "test_author"
    IMPLEMENTER = "implementer"
    VERIFIER = "verifier"


_ROLE_CAPABILITY = {
    RunRole.TEST_AUTHOR: "test_authoring",
    RunRole.IMPLEMENTER: "implementation",
    RunRole.VERIFIER: "verification",
}

# Every capability that identifies some role. Rule 1 strips the OTHER roles'
# entries out of a task's list before matching.
_ROLE_CAPABILITIES = frozenset(_ROLE_CAPABILITY.values())

# The declared `AgentSpec.role` strings that mean a specific run role. Anything
# else — "configured", "reviewer", whatever an operator typed — is NEUTRAL and
# stays eligible, so a registry that never adopted these labels still resolves
# instead of blocking. Only a declared role belonging to a DIFFERENT run role
# disqualifies an agent.
_DECLARED_ROLES = {role.value: role for role in RunRole}


def _requirements(role: RunRole, required_capabilities: list[str]) -> list[str]:
    """This role's own capability plus the task's non-role capabilities."""
    domain_capabilities = [
        capability
        for capability in required_capabilities
        if capability not in _ROLE_CAPABILITIES
    ]
    return [_ROLE_CAPABILITY[role], *domain_capabilities]


def _tiers(role: RunRole, catalog: list[AgentSpec]) -> list[list[AgentSpec]]:
    """Candidate tiers, most appropriate first.

    1. Declares exactly this run role.
    2. Declares no run role at all — "configured", "reviewer", whatever an
       operator typed. Neutral, so a registry that never adopted these labels
       resolves normally instead of blocking.

    An agent declaring a DIFFERENT run role appears in neither tier and can
    never be selected. There is deliberately no third, permissive tier: an
    agent that calls itself a `test_author` is not an implementer of last
    resort, and binding one anyway does not degrade gracefully — it fails as an
    agent-quality problem three layers from the cause, which is exactly what
    made the P8.4 defect cost a full cycle to find. `seed demo` now registers a
    real pair, so the default installation satisfies both roles honestly rather
    than by making the resolver permissive.
    """
    tiers: list[list[AgentSpec]] = [[], []]
    for agent in catalog:
        declared = _DECLARED_ROLES.get(agent.role)
        if declared is role:
            tiers[0].append(agent)
        elif declared is None:
            tiers[1].append(agent)
    return tiers


def resolve_role_agent(
    role: RunRole,
    required_capabilities: list[str],
    agents: AgentRepository,
) -> AgentSpec:
    """Use the configured registry; a role capability is mandatory, never defaulted.

    Deterministic: agents declaring this role are considered before neutral
    ones, and within each tier the registry's own order decides — so the same
    registry always yields the same binding, whatever order it was built in.
    """
    required = _requirements(role, required_capabilities)
    for tier in _tiers(role, agents.list()):
        agent_id = matching_agent_id(required, tier)
        if agent_id is not None:
            return agents.get(agent_id)
    raise RoleUnsatisfiableError(role.value, required)


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
