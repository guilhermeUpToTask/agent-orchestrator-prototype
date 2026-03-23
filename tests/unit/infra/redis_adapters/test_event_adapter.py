import json
import pathlib
from unittest.mock import patch

import fakeredis
import pytest

from src.infra.redis_adapters.event_adapter import RedisEventAdapter
from src.domain import DomainEvent

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

def test_subscribe_many_yields_deserialized_event(adapter, redis_client):
    """subscribe_many() should deserialise the raw Redis message into a DomainEvent."""
    event = DomainEvent(type="task.created", producer="p1", payload={"task_id": "t1"})

    # Sentinel exception to break out of the generator's internal while-True loop
    # cleanly without misusing KeyboardInterrupt (a real signal, not a test tool).
    class _Done(Exception):
        pass

    with patch.object(redis_client, "xreadgroup") as mock_read:
        mock_read.side_effect = [
            [(b"events:task.created", [(b"msg-1", {b"data": json.dumps(event.model_dump(mode="json"))})])],
            _Done(),
        ]

        gen = adapter.subscribe_many(["task.created"], "grp-test", "consumer-1")
        received = next(gen)

        assert received.type == "task.created"
        assert received.payload["task_id"] == "t1"

        # Exhaust the second call (raises _Done — expected)
        with pytest.raises(_Done):
            next(gen)

def test_subscribe_many_accepts_string_fields(adapter, redis_client):
    """subscribe_many() should also handle fakeredis/plain dict payloads with str keys."""
    event = DomainEvent(type="task.created", producer="p1", payload={"task_id": "t1"})

    with patch.object(redis_client, "xreadgroup") as mock_read, patch.object(
        redis_client, "xack"
    ) as mock_ack:
        mock_read.return_value = [
            ("events:task.created", [("msg-1", {"data": json.dumps(event.model_dump(mode="json"))})])
        ]

        gen = adapter.subscribe_many(["task.created"], "grp-test", "consumer-1")
        received = next(gen)

        assert received.type == "task.created"
        assert received.payload["task_id"] == "t1"
        gen.close()
        mock_ack.assert_called_once_with("events:task.created", "grp-test", "msg-1")

def test_subscribe_many_acks_before_reraising_invalid_payload(adapter, redis_client):
    """Invalid payloads should still be ACKed so Redis does not redeliver poison messages."""
    with patch.object(redis_client, "xreadgroup") as mock_read, patch.object(
        redis_client, "xack"
    ) as mock_ack:
        mock_read.return_value = [
            (b"events:task.created", [(b"msg-1", {b"data": b"{not-json"})])
        ]

        gen = adapter.subscribe_many(["task.created"], "grp-test", "consumer-1")

        with pytest.raises(json.JSONDecodeError):
            next(gen)

        mock_ack.assert_called_once_with("events:task.created", "grp-test", b"msg-1")

@pytest.mark.asyncio
async def test_publish_journal_created(adapter, tmp_path):
    event = DomainEvent(type="task.created", producer="p1", payload={"task_id": "t1"})
    adapter.publish(event)
    
    journal_files = list((tmp_path / "events").glob("*.json"))
    assert len(journal_files) == 1

def test_publish_ignores_journal_write_failure(adapter, redis_client, monkeypatch):
    """Publishing should still succeed even if the optional journal write fails."""
    event = DomainEvent(type="task.created", producer="p1", payload={"task_id": "t1"})

    def fail_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pathlib.Path, "write_text", fail_write)

    adapter.publish(event)

    msg = redis_client.xread({"events:task.created": "0"})
    msg_all = redis_client.xread({"events:all": "0"})

    assert len(msg) == 1
    assert len(msg_all) == 1
