"""
src/api/sse.py — Server-Sent Events broker with per-client fan-out.

Every connected SSE client gets its own queue, registered by the /events
route and unregistered on disconnect — two browser tabs both receive every
event instead of stealing from a shared queue.

publish() is thread-safe: API routers run in the threadpool (sync handlers)
and the Redis event bridge runs on its own thread, so off-loop callers are
the norm. The broker captures the event loop at lifespan startup and routes
off-loop publishes through loop.call_soon_threadsafe.

Routers keep calling the module-level publish_sse() shim.
"""

from __future__ import annotations

import asyncio
import threading

import structlog

log = structlog.get_logger(__name__)

_CLIENT_QUEUE_MAXSIZE = 200


class SSEBroker:
    def __init__(self) -> None:
        self._clients: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the server's event loop (call from lifespan startup)."""
        self._loop = loop

    def register(self) -> asyncio.Queue:
        """Create and track a queue for one SSE client connection."""
        q: asyncio.Queue = asyncio.Queue(maxsize=_CLIENT_QUEUE_MAXSIZE)
        with self._lock:
            self._clients.add(q)
        log.debug("sse.client_registered", clients=len(self._clients))
        return q

    def unregister(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._clients.discard(q)
        log.debug("sse.client_unregistered", clients=len(self._clients))

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish(self, event_type: str, payload: dict) -> None:
        """Fan an event out to every connected client. Thread-safe."""
        event = {"type": event_type, "payload": payload}
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:
                self._broadcast(event)
            else:
                # Off-loop caller (threadpool router or bridge thread):
                # asyncio.Queue is not thread-safe, hop onto the loop.
                loop.call_soon_threadsafe(self._broadcast, event)
            return
        # No loop bound (unit tests, CLI imports): best-effort direct put.
        self._broadcast(event)

    def _broadcast(self, event: dict) -> None:
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("sse.client_queue_full", event_type=event["type"])


# Module-level singleton — routers and the bridge share it.
broker = SSEBroker()


def publish_sse(event_type: str, payload: dict) -> None:
    """Back-compat shim over SSEBroker.publish()."""
    broker.publish(event_type, payload)


def get_broker() -> SSEBroker:
    return broker
