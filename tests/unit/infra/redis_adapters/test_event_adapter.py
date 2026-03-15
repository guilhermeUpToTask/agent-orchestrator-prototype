import pytest
from src.core.models import DomainEvent
from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter

def make_event(event_type: str = "task.created", task_id: str = "t1") -> DomainEvent:
    return DomainEvent(type=event_type, producer="test", payload={"task_id": task_id})

class TestInMemoryEventAdapter:
    def test_publish_and_all_events(self):
        adapter = InMemoryEventAdapter()
        evt = make_event()
        adapter.publish(evt)
        assert len(adapter.all_events) == 1
        assert adapter.all_events[0].type == "task.created"

    def test_events_of_type(self):
        adapter = InMemoryEventAdapter()
        adapter.publish(make_event("type.a"))
        adapter.publish(make_event("type.b"))
        assert len(adapter.events_of_type("type.a")) == 1

    def test_subscribe(self):
        adapter = InMemoryEventAdapter()
        adapter.publish(make_event("type.a", "t1"))
        adapter.publish(make_event("type.b", "t2"))
        events = list(adapter.subscribe("type.a"))
        assert len(events) == 1
        assert events[0].payload["task_id"] == "t1"

    def test_subscribe_many(self):
        adapter = InMemoryEventAdapter()
        adapter.publish(make_event("type.a", "t1"))
        adapter.publish(make_event("type.b", "t2"))
        events = list(adapter.subscribe_many(["type.a", "type.b"]))
        assert len(events) == 2
