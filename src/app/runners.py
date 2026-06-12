"""
src/app/runners.py — long-running coordinator loops, host-agnostic.

Each runner drives one coordinator daemon (task manager, goal orchestrator,
reconciler) against ports only, so the same function can run:
  - on a daemon thread inside the FastAPI process (the default topology), or
  - as the body of a standalone CLI process (`system task-manager`, etc.).

Stop semantics:
  - run_task_manager_loop takes a zero-arg `stop` callable and returns
    shortly after it turns true.
  - The goal orchestrator and reconciler own their loops already; their
    hosts stop them by calling .shutdown() on the object directly.

Redis-backed subscriptions observe `stop` between blocked reads; the
in-memory adapter's finite generators are re-entered by an outer poll loop
(its per-group cursors make that resumable).
"""
from __future__ import annotations

import time
from typing import Callable

import structlog

from src.domain import EventPort

log = structlog.get_logger(__name__)

TASK_MANAGER_GROUP = "task-manager"
TASK_MANAGER_EVENTS = [
    "task.created",
    "task.requeued",
    "task.completed",
    "task.failed",
    "task.execution_started",
    "task.execution_succeeded",
    "task.execution_failed",
]

_POLL_INTERVAL = 0.5  # outer-loop sleep when a finite generator drains


def run_task_manager_loop(
    handler,
    events: EventPort,
    stop: Callable[[], bool],
    consumer: str = "tm-1",
) -> None:
    """Route task lifecycle events to the TaskManagerHandler until stop()."""
    handlers = {
        # Lifecycle notifications carry only a task_id.
        "task.created": lambda e: handler.handle_task_created(e.payload["task_id"]),
        "task.requeued": lambda e: handler.handle_task_requeued(e.payload["task_id"]),
        "task.completed": lambda e: handler.handle_task_completed(e.payload["task_id"]),
        "task.failed": lambda e: handler.handle_task_failed(e.payload["task_id"]),
        # Worker execution results carry the full result payload.
        "task.execution_started": lambda e: handler.handle_execution_started(e.payload),
        "task.execution_succeeded": lambda e: handler.handle_execution_succeeded(e.payload),
        "task.execution_failed": lambda e: handler.handle_execution_failed(e.payload),
    }

    log.info("task_manager.started", events=list(handlers))
    while not stop():
        for event in events.subscribe_many(
            list(handlers), group=TASK_MANAGER_GROUP, consumer=consumer, stop=stop
        ):
            fn = handlers.get(event.type)
            if fn is None or not event.payload.get("task_id"):
                continue
            try:
                fn(event)
            except Exception as exc:
                # Log and move on; the un-acked entry stays in the PEL and is
                # replayed by the recovery pass on the next (re)start.
                log.exception(
                    "task_manager.handler_failed",
                    event_type=event.type,
                    task_id=event.payload.get("task_id"),
                    error=str(exc),
                )
                continue
            events.ack(event, group=TASK_MANAGER_GROUP)
        if not stop():
            time.sleep(_POLL_INTERVAL)
    log.info("task_manager.stopped")


def run_goal_orchestrator_loop(orchestrator) -> None:
    """Run the TaskGraphOrchestrator without touching signal handlers.

    Safe on any thread; stop it via orchestrator.shutdown().
    """
    orchestrator.run_forever(install_signal_handlers=False)


def run_reconciler_loop(reconciler) -> None:
    """Run the Reconciler; stop it via reconciler.shutdown()."""
    reconciler.run_forever()
