"""Real Clock adapter — the only place the wall clock is read for domain logic.

The domain scan takes ``now`` as an argument (stays pure); use cases get it from
the injected Clock port. Always timezone-aware UTC: naive datetimes would break
the ``retry_not_before <= now`` comparisons with a naive-vs-aware TypeError.
"""
from __future__ import annotations

from datetime import datetime, timezone


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
