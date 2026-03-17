"""
src/infra/redis_adapters/lease_adapter.py — Redis-backed LeasePort.

Key schema:
  lease:task:{task_id}  →  JSON { agent_id, lease_token }  TTL = lease_seconds
  lease:token:{token}   →  task_id  (reverse lookup for revoke)
"""
from __future__ import annotations

import json
from typing import Optional
from uuid import uuid4

import redis

from src.domain import LeasePort


class RedisLeaseAdapter(LeasePort):

    def __init__(self, redis_client: redis.Redis) -> None:
        self._r = redis_client

    # ------------------------------------------------------------------
    # LeasePort
    # ------------------------------------------------------------------

    def create_lease(self, task_id: str, agent_id: str, lease_seconds: int) -> str:
        token = str(uuid4())
        payload = json.dumps({"agent_id": agent_id, "lease_token": token})
        pipe = self._r.pipeline()
        pipe.setex(self._task_key(task_id), lease_seconds, payload)
        pipe.setex(self._token_key(token), lease_seconds, task_id)
        pipe.execute()
        return token

    def refresh_lease(self, lease_token: str, additional_seconds: int = 60) -> bool:
        task_id_bytes = self._r.get(self._token_key(lease_token))
        if not task_id_bytes:
            return False
        task_id = task_id_bytes.decode()
        # Extend both keys
        pipe = self._r.pipeline()
        pipe.expire(self._task_key(task_id), additional_seconds)
        pipe.expire(self._token_key(lease_token), additional_seconds)
        results = pipe.execute()
        return all(results)

    def revoke_lease(self, lease_token: str) -> bool:
        task_id_bytes = self._r.get(self._token_key(lease_token))
        pipe = self._r.pipeline()
        pipe.delete(self._token_key(lease_token))
        if task_id_bytes:
            pipe.delete(self._task_key(task_id_bytes.decode()))
        results = pipe.execute()
        return bool(results[0])

    def is_lease_active(self, task_id: str) -> bool:
        return bool(self._r.exists(self._task_key(task_id)))

    def get_lease_agent(self, task_id: str) -> Optional[str]:
        data = self._r.get(self._task_key(task_id))
        if not data:
            return None
        payload = json.loads(data)
        return payload.get("agent_id")

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _task_key(task_id: str) -> str:
        return f"lease:task:{task_id}"

    @staticmethod
    def _token_key(token: str) -> str:
        return f"lease:token:{token}"
