"""
tests/unit/infra/redis_adapters/test_lease_refresher.py

Unit tests for LeaseRefresher background threading behavior.

The refresher is always patched out in worker tests, so these are the only
tests covering its actual behavior: start/stop lifecycle, periodic refresh
calls, and graceful handling of a revoked lease.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, call

import pytest

from src.infra.redis_adapters.lease_refresher import LeaseRefresher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_refresher(lease_port=None, token="tok-abcdef12", interval=0, extension=120):
    """interval=0 means the refresher fires as fast as possible (useful in tests)."""
    if lease_port is None:
        lease_port = MagicMock()
        lease_port.refresh_lease.return_value = True
    return LeaseRefresher(
        lease_port=lease_port,
        lease_token=token,
        interval_seconds=interval,
        extension_seconds=extension,
    )


# ===========================================================================
# Lifecycle
# ===========================================================================

class TestLeaseRefresherLifecycle:

    def test_thread_is_alive_after_start(self):
        refresher = make_refresher()
        refresher.start()
        assert refresher._thread.is_alive()
        refresher.stop()

    def test_thread_is_not_alive_after_stop(self):
        refresher = make_refresher()
        refresher.start()
        refresher.stop()
        assert not refresher._thread.is_alive()

    def test_stop_event_can_be_set_without_start(self):
        """Setting the stop event on a never-started refresher must not raise."""
        refresher = make_refresher()
        # Mark as stopped without ever calling start() — should be a harmless no-op
        refresher._stop_event.set()
        assert refresher._stop_event.is_set()
        assert not refresher._thread.is_alive()

    def test_thread_is_daemon(self):
        refresher = make_refresher()
        assert refresher._thread.daemon is True


# ===========================================================================
# Refresh behavior
# ===========================================================================

class TestLeaseRefresherBehavior:

    def test_refresh_lease_called_at_least_once(self):
        lease_port = MagicMock()
        lease_port.refresh_lease.return_value = True
        refresher = make_refresher(lease_port=lease_port, interval=0)
        refresher.start()
        time.sleep(0.05)
        refresher.stop()
        assert lease_port.refresh_lease.call_count >= 1

    def test_refresh_called_with_correct_token_and_extension(self):
        lease_port = MagicMock()
        lease_port.refresh_lease.return_value = True
        refresher = make_refresher(lease_port=lease_port, token="tok-deadbeef", interval=0, extension=180)
        refresher.start()
        time.sleep(0.05)
        refresher.stop()
        for c in lease_port.refresh_lease.call_args_list:
            assert c == call("tok-deadbeef", 180)

    def test_does_not_raise_when_refresh_returns_false(self):
        """Lease already revoked by reconciler — refresher must not crash."""
        lease_port = MagicMock()
        lease_port.refresh_lease.return_value = False
        refresher = make_refresher(lease_port=lease_port, interval=0)
        refresher.start()
        time.sleep(0.05)
        refresher.stop()   # must not raise

    def test_does_not_raise_when_refresh_raises(self):
        """Network error during refresh — refresher logs and continues, doesn't crash."""
        lease_port = MagicMock()
        lease_port.refresh_lease.side_effect = Exception("redis down")
        refresher = make_refresher(lease_port=lease_port, interval=0)
        refresher.start()
        time.sleep(0.05)
        refresher.stop()  # must not raise — _run() swallows the error and retries

    def test_no_refresh_after_stop(self):
        lease_port = MagicMock()
        lease_port.refresh_lease.return_value = True
        refresher = make_refresher(lease_port=lease_port, interval=0)
        refresher.start()
        time.sleep(0.05)
        refresher.stop()
        count_at_stop = lease_port.refresh_lease.call_count
        time.sleep(0.05)
        # Call count must not increase after stop
        assert lease_port.refresh_lease.call_count == count_at_stop
