import json
import fakeredis
import pytest
from src.infra.redis_adapters.event_adapter import RedisEventAdapter
from src.core.models import DomainEvent

@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis()

@pytest.fixture
def adapter(redis_client, tmp_path):
    return RedisEventAdapter(redis_client, journal_dir=str(tmp_path / "events"))

def test_publish(adapter, redis_client):
    event = DomainEvent(type="task.created", producer="p1", payload={"task_id": "t1"})
    adapter.publish(event)
    
    # Check individual stream
    msg = redis_client.xread({"events:task.created": "0"})
    assert len(msg) == 1
    data = json.loads(msg[0][1][0][1][b"data"])
    assert data["payload"]["task_id"] == "t1"
    
    # Check global stream
    msg_all = redis_client.xread({"events:all": "0"})
    assert len(msg_all) == 1

from unittest.mock import patch, MagicMock

def test_subscribe_many(adapter, redis_client):
    event = DomainEvent(type="t1", producer="p1", payload={"task_id": "t1"})
    
    # We mock xreadgroup to return one event and then empty list to break the iterator
    # However RedisEventAdapter has 'while True', so we need to raise an exception or similar
    # to exit the test if it loops.
    # Alternatively, we test the logic that processes the results.
    with patch.object(redis_client, "xreadgroup") as mock_read:
        mock_read.side_effect = [
            [(b"events:t1", [(b"msg-1", {b"data": json.dumps(event.model_dump(mode="json"))})])],
            KeyboardInterrupt() # stop the while True loop
        ]
        
        with pytest.raises(KeyboardInterrupt):
            gen = adapter.subscribe_many(["t1"], "g1", "c1")
            received = next(gen)
            assert received.type == "t1"
            next(gen) # triggers second call which raises

@pytest.mark.asyncio
async def test_publish_journal_created(adapter, tmp_path):
    event = DomainEvent(type="task.created", producer="p1", payload={"task_id": "t1"})
    adapter.publish(event)
    
    journal_files = list((tmp_path / "events").glob("*.json"))
    assert len(journal_files) == 1
