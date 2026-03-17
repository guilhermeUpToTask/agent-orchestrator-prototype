"""
src/domain/repositories/agent_registry.py — Agent registry port.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.entities.agent import AgentProps


class AgentRegistryPort(ABC):
    """
    Contract for registering agents, updating heartbeats, and querying the registry.
    Infrastructure provides adapters (JSON file, Redis, etc.).
    """

    @abstractmethod
    def register(self, agent: AgentProps) -> None:
        """Add or update an agent entry in the registry."""
        ...

    @abstractmethod
    def deregister(self, agent_id: str) -> None:
        """Remove an agent from the registry."""
        ...

    @abstractmethod
    def list_agents(self) -> list[AgentProps]:
        """Return all registered agents."""
        ...

    @abstractmethod
    def heartbeat(self, agent_id: str) -> None:
        """Update the last_heartbeat timestamp for the given agent."""
        ...

    @abstractmethod
    def get(self, agent_id: str) -> Optional[AgentProps]:
        """Return an agent by ID, or None if not registered."""
        ...
