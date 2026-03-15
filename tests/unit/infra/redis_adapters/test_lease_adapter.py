import json
import fakeredis
import pytest
from src.infra.redis_adapters.lease_adapter import RedisLeaseAdapter

@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis()

@pytest.fixture
def adapter(redis_client):
    return RedisLeaseAdapter(redis_client)

def test_create_lease(adapter, redis_client):
    token = adapter.create_lease("t1", "a1", 60)
    assert token is not None
    assert redis_client.exists(f"lease:task:t1")
    assert redis_client.exists(f"lease:token:{token}")

def test_refresh_lease(adapter, redis_client):
    token = adapter.create_lease("t1", "a1", 60)
    ok = adapter.refresh_lease(token, 120)
    assert ok is True
    assert redis_client.ttl(f"lease:task:t1") > 60

def test_revoke_lease(adapter, redis_client):
    token = adapter.create_lease("t1", "a1", 60)
    adapter.revoke_lease(token)
    assert not redis_client.exists(f"lease:task:t1")
    assert not redis_client.exists(f"lease:token:{token}")

def test_is_lease_active(adapter):
    adapter.create_lease("t1", "a1", 60)
    assert adapter.is_lease_active("t1") is True
    assert adapter.is_lease_active("non-existent") is False

def test_get_lease_agent(adapter):
    adapter.create_lease("t1", "a1", 60)
    assert adapter.get_lease_agent("t1") == "a1"
    assert adapter.get_lease_agent("non-existent") is None
