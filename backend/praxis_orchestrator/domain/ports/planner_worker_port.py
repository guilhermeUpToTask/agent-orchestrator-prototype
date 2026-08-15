"""The worker-cadence port: injected time.

Domain code never reads a clock — `now` is always passed in (the navigation
scan takes it as a parameter). This port is how the application/worker layer
obtains that `now`, and how tests make time deterministic (FakeClock).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Injected time source. Keeps the domain scan pure (now is passed in) and
    makes time deterministically controllable in tests."""

    def now(self) -> "datetime": ...
