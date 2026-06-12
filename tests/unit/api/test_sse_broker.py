"""
tests/unit/api/test_sse_broker.py — SSEBroker fan-out and thread safety,
plus the domain-event → SSE mapping table.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from src.api.event_bridge import map_domain_event_to_sse
from src.api.sse import SSEBroker


class TestBrokerFanOut:
    def test_every_registered_client_receives_every_event(self):
        broker = SSEBroker()
        q1 = broker.register()
        q2 = broker.register()

        broker.publish("task.status_changed", {"task_id": "t1", "status": "succeeded"})

        for q in (q1, q2):
            event = q.get_nowait()
            assert event == {
                "type": "task.status_changed",
                "payload": {"task_id": "t1", "status": "succeeded"},
            }

    def test_unregistered_client_stops_receiving(self):
        broker = SSEBroker()
        q1 = broker.register()
        q2 = broker.register()
        broker.unregister(q1)

        broker.publish("plan.status_changed", {"status": "done"})

        assert q1.empty()
        assert not q2.empty()

    def test_full_client_queue_drops_without_blocking_others(self):
        broker = SSEBroker()
        q_full = broker.register()
        q_ok = broker.register()
        while not q_full.full():
            q_full.put_nowait({"type": "noise", "payload": {}})

        broker.publish("goal.pr_state_synced", {"goal_id": "g1"})

        assert q_ok.get_nowait()["type"] == "goal.pr_state_synced"

    @pytest.mark.asyncio
    async def test_off_loop_publish_hops_onto_the_bound_loop(self):
        broker = SSEBroker()
        broker.bind_loop(asyncio.get_running_loop())
        q = broker.register()

        t = threading.Thread(
            target=broker.publish, args=("task.status_changed", {"task_id": "t1"})
        )
        t.start()
        t.join()

        event = await asyncio.wait_for(q.get(), timeout=2.0)
        assert event["type"] == "task.status_changed"


class TestDomainEventMapping:
    @pytest.mark.parametrize(
        "domain_type,status",
        [
            ("task.created", "created"),
            ("task.assigned", "assigned"),
            ("task.started", "in_progress"),
            ("task.completed", "succeeded"),
            ("task.failed", "failed"),
            ("task.requeued", "requeued"),
            ("task.canceled", "canceled"),
        ],
    )
    def test_task_lifecycle_maps_to_status_changed(self, domain_type, status):
        mapped = map_domain_event_to_sse(domain_type, {"task_id": "t1"})
        assert mapped == ("task.status_changed", {"task_id": "t1", "status": status})

    def test_worker_internal_protocol_events_are_dropped(self):
        for et in (
            "task.execution_started",
            "task.execution_succeeded",
            "task.execution_failed",
        ):
            assert map_domain_event_to_sse(et, {"task_id": "t1"}) is None

    def test_goal_pr_opened_passes_through(self):
        mapped = map_domain_event_to_sse(
            "goal.pr_opened", {"goal_id": "g1", "pr_number": 7}
        )
        assert mapped == ("goal.pr_opened", {"goal_id": "g1", "pr_number": 7})

    def test_other_goal_events_become_goals_invalidation(self):
        for et in ("goal.ready_for_review", "goal.approved", "goal.merged"):
            assert map_domain_event_to_sse(et, {"goal_id": "g1"}) == (
                "goal.pr_state_synced",
                {"goal_id": "g1"},
            )

    def test_plan_and_unknown_events_forward_as_is(self):
        assert map_domain_event_to_sse("plan.jit_progress", {"x": 1}) == (
            "plan.jit_progress",
            {"x": 1},
        )
        assert map_domain_event_to_sse("something.new", {"y": 2}) == (
            "something.new",
            {"y": 2},
        )


class TestBridgeIntegration:
    def test_domain_publish_reaches_registered_sse_queue(self, tmp_path):
        import fakeredis

        from src.api.event_bridge import run_event_bridge
        from src.domain import DomainEvent
        from src.infra.redis_adapters.event_adapter import RedisEventAdapter

        r = fakeredis.FakeRedis()
        adapter = RedisEventAdapter(r, journal_dir=str(tmp_path / "events"))
        broker = SSEBroker()
        q = broker.register()

        stop = threading.Event()
        bridge = threading.Thread(
            target=run_event_bridge, args=(r, broker, stop.is_set), daemon=True
        )
        bridge.start()
        try:
            import time

            time.sleep(0.1)  # let the bridge pass its first "$" read
            adapter.publish(
                DomainEvent(
                    type="task.completed",
                    producer="task-manager",
                    payload={"task_id": "t1", "commit_sha": "abc"},
                )
            )
            deadline = time.monotonic() + 3
            event = None
            while time.monotonic() < deadline:
                try:
                    event = q.get_nowait()
                    break
                except asyncio.QueueEmpty:
                    time.sleep(0.02)
            assert event == {
                "type": "task.status_changed",
                "payload": {"task_id": "t1", "status": "succeeded"},
            }
        finally:
            stop.set()
            bridge.join(timeout=3)
