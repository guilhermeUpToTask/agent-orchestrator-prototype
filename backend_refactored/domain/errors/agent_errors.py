from __future__ import annotations

from domain.errors.base import DomainError


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


class NoDefaultAgentError(DomainError):
    code = "NO_DEFAULT_AGENT"

    def __init__(self) -> None:
        super().__init__("No default agent is configured to fall back to.")
