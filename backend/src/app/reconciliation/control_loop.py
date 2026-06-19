"""
src/app/reconciliation/control_loop.py — Reconcile control-loop framework.

A ``ControlLoop`` owns the invariants for ONE layer/aggregate and reconciles
desired-state (what the authoritative aggregate declares) against observed-state
(what the repositories actually hold), idempotently. The ``ReconcilerScheduler``
runs many loops — each at its own cadence — under one shared harness that gives
them uniform telemetry, per-loop exponential backoff, and an optional
single-writer guard.

This is the Kubernetes-controller split: federated scopes (one loop per layer),
shared manager (one scheduler). A monolithic reconciler that "checks every
layer" would have to depend on every layer's ports — a god-object that inverts
the dependency rule. Federated loops each stay within their own layer; the
scheduler knows nothing about what any loop does.

Loops are LEVEL-triggered: ``reconcile_once`` re-derives desired vs observed
from state on every pass and must never rely on an event having fired. That is
precisely what makes them a safety net for the event-driven fast path — a lost
or never-emitted event (the orphan-goal failure mode) is still healed because
the next sweep observes the divergence directly.
"""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from typing import Callable, Optional

import structlog

log = structlog.get_logger(__name__)


class ControlLoop(ABC):
    """A single reconcile loop: observe → diff → act, idempotently.

    Subclasses set ``name`` (stable id for telemetry/scheduling) and
    ``interval_seconds`` (cadence), and implement ``reconcile_once``.

    ``reconcile_once`` MUST be idempotent and safe to call repeatedly: skip when
    already in the desired state, and use aggregate transition methods + CAS for
    any writes — never mutate fields directly.
    """

    #: Stable identifier used in telemetry and scheduling.
    name: str = "control_loop"
    #: How often the scheduler should invoke this loop, in seconds.
    interval_seconds: float = 60.0

    @abstractmethod
    def reconcile_once(self) -> None:
        """Run exactly one reconcile pass for this loop's scope."""
        ...


class ReconcilerScheduler:
    """Runs a set of ControlLoops, each at its own cadence, on one thread.

    Cross-cutting concerns the individual loops should NOT re-implement:
      * timing      — each loop fires every ``interval_seconds``
      * isolation   — an exception in one loop never stops the others
      * backoff     — a failing loop is retried with exponential backoff to a cap
      * telemetry   — uniform ``reconciler.loop.*`` events per pass
      * single-writer — an optional guard (e.g. a distributed lease) gates each
        pass so only one process acts on a given scope at a time
    """

    def __init__(
        self,
        loops: list[ControlLoop],
        *,
        max_backoff_seconds: float = 600.0,
        tick_seconds: float = 1.0,
        single_writer_guard: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self._loops = list(loops)
        self._max_backoff = max_backoff_seconds
        self._tick = max(0.1, tick_seconds)
        self._guard = single_writer_guard
        self._backoff: dict[str, float] = {loop.name: 0.0 for loop in self._loops}
        self._stop = threading.Event()

    def run_once(self) -> None:
        """Run every loop exactly once (used by tests and manual triggers)."""
        for loop in self._loops:
            self._run_loop_safely(loop)

    def run_forever(self) -> None:
        """Run until ``shutdown()``.

        Each loop waits one full interval before its first pass — boot grace, so
        a sweep doesn't act on state that in-flight workers haven't reported yet.
        """
        now = time.monotonic()
        next_due = {loop.name: now + loop.interval_seconds for loop in self._loops}
        log.info("reconciler.scheduler.started", loops=[loop.name for loop in self._loops])
        while not self._stop.wait(self._tick):
            now = time.monotonic()
            for loop in self._loops:
                if now >= next_due[loop.name]:
                    self._run_loop_safely(loop)
                    interval = loop.interval_seconds + self._backoff[loop.name]
                    next_due[loop.name] = time.monotonic() + interval
        log.info("reconciler.scheduler.stopped")

    def shutdown(self) -> None:
        """Unblock ``run_forever()`` at the next tick."""
        self._stop.set()

    def _run_loop_safely(self, loop: ControlLoop) -> None:
        if self._guard is not None and not self._guard(loop.name):
            log.debug("reconciler.loop.skipped_not_leader", loop=loop.name)
            return
        start = time.monotonic()
        try:
            loop.reconcile_once()
        except Exception as exc:
            prev = self._backoff[loop.name] or loop.interval_seconds
            self._backoff[loop.name] = min(prev * 2, self._max_backoff)
            log.exception(
                "reconciler.loop.error",
                loop=loop.name,
                error=str(exc),
                backoff_seconds=self._backoff[loop.name],
            )
            return
        self._backoff[loop.name] = 0.0
        log.info(
            "reconciler.loop.pass",
            loop=loop.name,
            duration_ms=round((time.monotonic() - start) * 1000, 1),
        )
