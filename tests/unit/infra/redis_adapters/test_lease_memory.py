import pytest
from src.infra.redis_adapters.lease_memory import InMemoryLeaseAdapter

class TestInMemoryLeaseAdapter:
    def test_create_returns_token(self):
        adapter = InMemoryLeaseAdapter()
        token = adapter.create_lease("task-1", "agent-1", 300)
        assert token is not None and len(token) > 0

    def test_is_active(self):
        adapter = InMemoryLeaseAdapter()
        adapter.create_lease("task-1", "agent-1", 300)
        assert adapter.is_lease_active("task-1") is True
        assert adapter.is_lease_active("unknown") is False

    def test_revoke(self):
        adapter = InMemoryLeaseAdapter()
        token = adapter.create_lease("task-1", "agent-1", 300)
        assert adapter.revoke_lease(token) is True
        assert adapter.is_lease_active("task-1") is False

    def test_refresh(self):
        adapter = InMemoryLeaseAdapter()
        token = adapter.create_lease("task-1", "agent-1", 1)
        assert adapter.refresh_lease(token, 300) is True
        assert adapter.is_lease_active("task-1") is True

    def test_get_agent(self):
        adapter = InMemoryLeaseAdapter()
        adapter.create_lease("task-1", "agent-007", 300)
        assert adapter.get_lease_agent("task-1") == "agent-007"
