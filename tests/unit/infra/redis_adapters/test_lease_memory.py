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
