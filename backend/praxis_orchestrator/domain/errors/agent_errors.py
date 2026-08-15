from __future__ import annotations

from praxis_orchestrator.domain.errors.base import DomainError


class UnknownCapabilityError(DomainError):
    code = "UNKNOWN_CAPABILITY"

    def __init__(self, tag: str, known: list[str]) -> None:
        self.tag = tag
        self.known = known
        known_str = ", ".join(known) if known else "(none registered)"
        super().__init__(
            f"Unknown capability '{tag}'. Register it first or use a known tag: {known_str}.",
            context={"tag": tag},
        )


class AgentNotFoundError(DomainError):
    """A task references an agent id that no longer exists (e.g. user deleted it).
    Reactive safety net complementing the proactive delete-guard."""

    code = "AGENT_NOT_FOUND"

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        super().__init__(
            f"Agent '{agent_id}' not found (it may have been deleted).",
            context={"agent_id": agent_id},
        )


class CapabilityNoLongerSatisfiedError(DomainError):
    """The bound agent no longer covers the task's required capabilities (user
    edited the agent after binding). Snapshot binding stays, but execution validates."""

    code = "CAPABILITY_NO_LONGER_SATISFIED"

    def __init__(self, task_id: str, agent_id: str, missing: list[str]) -> None:
        self.task_id = task_id
        self.agent_id = agent_id
        self.missing = missing
        super().__init__(
            f"Agent '{agent_id}' no longer satisfies task '{task_id}'. "
            f"Missing capabilities: {', '.join(missing)}.",
            context={"task_id": task_id, "agent_id": agent_id, "missing": missing},
        )


class RoleUnsatisfiableError(DomainError):
    """No registered agent covers a mandatory run role's capability set.

    Raised by role resolution, which is reached both during enrichment (where
    it becomes an `agent_capability` block) and from `retry_stage`, whose whole
    purpose is rebinding that block from a repaired registry. It must be a
    CODED DomainError: a bare builtin cannot be mapped by the API's single
    status table and surfaces to the operator as an opaque 500 instead of
    naming the capabilities they need to register.
    """

    code = "ROLE_UNSATISFIABLE"

    def __init__(self, role: str, required: list[str]) -> None:
        self.role = role
        self.required = required
        super().__init__(
            f"No configured agent covers {role}: {sorted(set(required))}. "
            "Register an agent with these capabilities, then retry the stage.",
            context={"role": role, "required": sorted(set(required))},
        )


class NoDefaultAgentError(DomainError):
    code = "NO_DEFAULT_AGENT"

    def __init__(self) -> None:
        super().__init__("No default agent is configured to fall back to.")
