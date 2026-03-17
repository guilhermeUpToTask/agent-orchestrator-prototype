"""
src/domain/ports/lease.py — Distributed lease port.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class LeasePort(ABC):
    """
    Contract for acquiring, refreshing, and revoking time-bound leases.
    A lease ties a task to a specific agent for a bounded window of time.
    Infrastructure provides adapters (Redis TTL keys, in-memory, etc.).
    """

    @abstractmethod
    def create_lease(self, task_id: str, agent_id: str, lease_seconds: int) -> str:
        """Create a lease and return an opaque lease_token."""
        ...

    @abstractmethod
    def refresh_lease(self, lease_token: str, additional_seconds: int = 60) -> bool:
        """Extend the lease expiry. Returns False if the lease no longer exists."""
        ...

    @abstractmethod
    def revoke_lease(self, lease_token: str) -> bool:
        """Revoke a lease immediately. Returns False if already expired."""
        ...

    @abstractmethod
    def is_lease_active(self, task_id: str) -> bool:
        """Return True if an unexpired lease exists for the given task."""
        ...

    @abstractmethod
    def get_lease_agent(self, task_id: str) -> Optional[str]:
        """Return the agent_id holding the active lease, or None."""
        ...
