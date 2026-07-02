from __future__ import annotations

from typing import Protocol

from src.domain.entities.agent_spec import AgentSpec


class AgentRepository(Protocol):
    """User-managed at runtime (full CRUD). delete() must be guarded: refuse if
    the agent is referenced by a non-terminal task (raises ReferencedEntityInUseError).
    Reactive safety net: get() raises AgentNotFoundError for dangling references."""

    def get(self, agent_id: str) -> AgentSpec: ...
    def list(self) -> list[AgentSpec]: ...
    def add(self, agent: AgentSpec) -> None: ...
    def update(self, agent: AgentSpec) -> None: ...
    def delete(self, agent_id: str) -> None: ...  # guarded
    def default_agent_id(self) -> str: ...  # raises NoDefaultAgentError if unset
