import time
import pytest
from src.infra.redis_adapters.lease_memory import InMemoryLeaseAdapter

@pytest.fixture
def adapter():
    return InMemoryLeaseAdapter()

def test_create_lease(adapter):
    token = adapter.create_lease("t1", "a1", 60)
    assert token is not None
    assert adapter.is_lease_active("t1")

def test_refresh_lease(adapter):
    token = adapter.create_lease("t1", "a1", 60)
    ok = adapter.refresh_lease(token, 120)
    assert ok is True

def test_revoke_lease(adapter):
    token = adapter.create_lease("t1", "a1", 60)
    adapter.revoke_lease(token)
    assert not adapter.is_lease_active("t1")

def test_is_lease_active_expiration(adapter):
    adapter.create_lease("t1", "a1", 0.01)
    time.sleep(0.02)
    assert not adapter.is_lease_active("t1")

def test_expire_all(adapter):
    adapter.create_lease("t1", "a1", 60)
    adapter.expire_all()
    assert not adapter.is_lease_active("t1")

def test_create_lease_replaces_existing_lease(adapter):
    """Creating a new lease for a task that already has one cleans up the old token."""
    token1 = adapter.create_lease("t1", "a1", 60)
    token2 = adapter.create_lease("t1", "a2", 60)
    # Old token must be gone
    assert adapter.refresh_lease(token1, 60) is False
    # New token must be valid
    assert adapter.refresh_lease(token2, 60) is True

def test_refresh_lease_returns_false_for_unknown_token(adapter):
    """refresh_lease returns False when the token doesn't exist (lease already revoked)."""
    assert adapter.refresh_lease("nonexistent-token", 60) is False

def test_get_lease_agent_returns_agent_id(adapter):
    """get_lease_agent returns the agent_id that holds the lease."""
    adapter.create_lease("t1", "agent-007", 60)
    assert adapter.get_lease_agent("t1") == "agent-007"

def test_get_lease_agent_returns_none_when_no_lease(adapter):
    """get_lease_agent returns None when the task has no active lease."""
    assert adapter.get_lease_agent("nonexistent") is None

def test_get_lease_agent_returns_none_after_expiry(adapter):
    """get_lease_agent returns None after the lease has expired."""
    adapter.create_lease("t1", "a1", 0.01)
    time.sleep(0.02)
    assert adapter.get_lease_agent("t1") is None
