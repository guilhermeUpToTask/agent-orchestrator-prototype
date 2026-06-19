"""
tests/unit/app/test_runners.py — coordinator runner loops.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from src.app.runners import run_task_manager_loop
from src.domain import DomainEvent
from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_task_manager_loop_processes_backlog_and_new_events_then_stops():
    events = InMemoryEventAdapter()
    handler = MagicMock()
    stop = threading.Event()

    events.publish(DomainEvent(type="task.created", producer="t", payload={"task_id": "t1"}))

    thread = threading.Thread(
        target=run_task_manager_loop, args=(handler, events, stop.is_set), daemon=True
    )
    thread.start()

    assert _wait_for(lambda: handler.handle_task_created.called)
    handler.handle_task_created.assert_called_once_with("t1")

    # Published while the loop is running: picked up by the re-subscribe pass.
    events.publish(DomainEvent(type="task.completed", producer="t", payload={"task_id": "t2"}))
    assert _wait_for(lambda: handler.handle_task_completed.called)
    handler.handle_task_completed.assert_called_once_with("t2")

    stop.set()
    thread.join(timeout=3)
    assert not thread.is_alive()


def test_task_manager_loop_survives_handler_exceptions():
    events = InMemoryEventAdapter()
    handler = MagicMock()
    handler.handle_task_created.side_effect = RuntimeError("boom")
    stop = threading.Event()

    events.publish(DomainEvent(type="task.created", producer="t", payload={"task_id": "bad"}))
    events.publish(DomainEvent(type="task.failed", producer="t", payload={"task_id": "t2"}))

    thread = threading.Thread(
        target=run_task_manager_loop, args=(handler, events, stop.is_set), daemon=True
    )
    thread.start()

    # The failing event does not kill the loop; the next one is processed.
    assert _wait_for(lambda: handler.handle_task_failed.called)
    handler.handle_task_failed.assert_called_once_with("t2")

    stop.set()
    thread.join(timeout=3)
    assert not thread.is_alive()


def test_task_manager_loop_ignores_events_without_task_id():
    events = InMemoryEventAdapter()
    handler = MagicMock()
    stop = threading.Event()

    events.publish(DomainEvent(type="task.created", producer="t", payload={}))
    events.publish(DomainEvent(type="task.created", producer="t", payload={"task_id": "ok"}))

    thread = threading.Thread(
        target=run_task_manager_loop, args=(handler, events, stop.is_set), daemon=True
    )
    thread.start()

    assert _wait_for(lambda: handler.handle_task_created.called)
    handler.handle_task_created.assert_called_once_with("ok")

    stop.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
