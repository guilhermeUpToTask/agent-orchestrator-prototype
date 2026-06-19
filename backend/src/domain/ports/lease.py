"""
src/domain/ports/lease.py — Distributed lease port.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Optional


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


class LeaseRefresherPort(ABC):
    """
    Contract for a background lease-keep-alive handle.

    The application layer only needs to start and stop the refresher — it
    should never depend on the threading or Redis implementation details that
    live in infra.  Infrastructure provides the concrete adapter
    (LeaseRefresher); the app layer works against this port.
    """

    @abstractmethod
    def start(self) -> None:
        """Begin background lease refreshing."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop background refreshing and wait for the thread to exit."""
        ...


# Factory type: given a (LeasePort, lease_token) pair, returns a refresher
# handle ready to be started.  The concrete factory lives in infra/factory.py
# and is injected into TaskExecuteUseCase at wiring time.
LeaseRefresherFactory = Callable[["LeasePort", str], LeaseRefresherPort]
