"""
src/infra/redis_adapters/lease_memory.py — In-memory LeasePort for tests.
"""
from __future__ import annotations

import time
from typing import Optional
from uuid import uuid4

from src.core.ports import LeasePort


class InMemoryLeaseAdapter(LeasePort):
    """Thread-unsafe in-process lease store. For unit/integration tests only."""

    def __init__(self) -> None:
        # task_id -> {agent_id, lease_token, expires_at}
        self._leases: dict[str, dict] = {}
        # token -> task_id
        self._tokens: dict[str, str] = {}

    def create_lease(self, task_id: str, agent_id: str, lease_seconds: int) -> str:
        # If a lease already exists for this task, evict the old token so
        # revoke_lease(old_token) correctly returns False after replacement.
        existing = self._leases.get(task_id)
        if existing:
            self._tokens.pop(existing["lease_token"], None)

        token = str(uuid4())
        self._leases[task_id] = {
            "agent_id": agent_id,
            "lease_token": token,
            "expires_at": time.monotonic() + lease_seconds,
        }
        self._tokens[token] = task_id
        return token

    def refresh_lease(self, lease_token: str, additional_seconds: int = 60) -> bool:
        task_id = self._tokens.get(lease_token)
        if not task_id or task_id not in self._leases:
            return False
        self._leases[task_id]["expires_at"] = time.monotonic() + additional_seconds
        return True

    def revoke_lease(self, lease_token: str) -> bool:
        task_id = self._tokens.pop(lease_token, None)
        if task_id:
            self._leases.pop(task_id, None)
            return True
        return False

    def is_lease_active(self, task_id: str) -> bool:
        lease = self._leases.get(task_id)
        if not lease:
            return False
        if time.monotonic() > lease["expires_at"]:
            # Expired — clean up
            token = lease["lease_token"]
            self._tokens.pop(token, None)
            self._leases.pop(task_id, None)
            return False
        return True

    def get_lease_agent(self, task_id: str) -> Optional[str]:
        if not self.is_lease_active(task_id):
            return None
        return self._leases[task_id]["agent_id"]

    def expire_all(self) -> None:
        """Test helper: immediately expire all leases."""
        for lease in self._leases.values():
            lease["expires_at"] = 0.0