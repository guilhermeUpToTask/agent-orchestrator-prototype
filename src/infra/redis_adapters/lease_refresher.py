"""
src/infra/redis_adapters/lease_refresher.py — Background lease-refresh daemon.

Moved here from src/app/handlers/worker.py as part of Phase 2 refactoring.
Infrastructure concerns (background Redis lease-keep-alive thread) belong in
the infra layer, not inside an application handler.

src/app/handlers/worker.py re-exports LeaseRefresher as _LeaseRefresher for
backward compatibility with existing tests and patches.
"""

from __future__ import annotations

import threading

import structlog

from src.domain import LeasePort

log = structlog.get_logger(__name__)

_LEASE_REFRESH_INTERVAL = 60
_LEASE_REFRESH_EXTENSION = 120


class LeaseRefresher:
    """
    Keeps a task lease alive in the background while the agent session runs.

    Spawns a daemon thread that calls lease_port.refresh_lease() every
    interval_seconds, extending the expiry by extension_seconds each time.
    The thread is a daemon so it does not prevent process shutdown.

    Call stop() before revoking the lease; otherwise the refresher may race
    with the final revoke and log spurious warnings.
    """

    def __init__(
        self,
        lease_port: LeasePort,
        lease_token: str,
        interval_seconds: int = _LEASE_REFRESH_INTERVAL,
        extension_seconds: int = _LEASE_REFRESH_EXTENSION,
    ) -> None:
        self._lease = lease_port
        self._token = lease_token
        self._interval = interval_seconds
        self._extension = extension_seconds
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"lease-refresher-{lease_token[:8]}",
        )

    def start(self) -> None:
        self._thread.start()
        log.debug("lease_refresher.started", token=self._token[:8])

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=5)
        log.debug("lease_refresher.stopped", token=self._token[:8])

    def _run(self) -> None:
        while not self._stop_event.wait(timeout=self._interval):
            ok = self._lease.refresh_lease(self._token, self._extension)
            if ok:
                log.debug("lease_refresher.refreshed", token=self._token[:8])
            else:
                log.warning(
                    "lease_refresher.refresh_failed",
                    token=self._token[:8],
                    reason="lease may have been revoked by reconciler",
                )
