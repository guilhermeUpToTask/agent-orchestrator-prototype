"""Is anything going to pick up work?

`/api/readiness` answers whether this machine *can* run a plan. Nothing answered
whether anything *will*: a plan claim and a goal lease both prove a worker is
BUSY, and an idle worker holds neither — so before the first claim, "worker
running, nothing to do" and "worker never started" were indistinguishable over
HTTP. That is the most common way a local install is silently half-configured,
and it is the first thing the J1/J2 setup checklist needs.

Staleness is asserted against an INJECTED clock, never wall time: the threshold
is a server-side judgement and a test that slept for it would be slow and flaky.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from agent_orchestrator.api import dependencies
from agent_orchestrator.api.routers.workers import STALE_AFTER_SECONDS
from agent_orchestrator.api.server import create_app
from agent_orchestrator.infra.container import AppContainer
from agent_orchestrator.infra.db.tables import Base

pytestmark = pytest.mark.integration

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class _FixedClock:
    """The container's clock, pinned so staleness is arithmetic, not waiting."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    clock = _FixedClock(NOW)
    monkeypatch.setitem(container.__dict__, "clock", clock)
    with TestClient(create_app(container)) as client:
        yield client, container, clock
    dependencies.set_container(None)  # type: ignore[arg-type]


async def _beat(container, worker_id: str = "worker-1") -> None:
    await container.worker_registry.beat(
        worker_id,
        mode="dry-run",
        poll_seconds=1.0,
        lease_seconds=30,
        max_concurrent_goals=4,
        inflight_goals=0,
        now=container.clock.now(),
    )


def test_no_worker_has_ever_reported(stack):
    client, _container, _clock = stack

    assert client.get("/api/workers").json() == []


def test_a_fresh_worker_is_not_stale(stack, anyio_backend=None):
    import asyncio

    client, container, _clock = stack
    asyncio.run(_beat(container))

    [row] = client.get("/api/workers").json()

    assert row["worker_id"] == "worker-1"
    assert row["mode"] == "dry-run"
    assert row["stale"] is False
    assert row["seconds_since_seen"] == pytest.approx(0.0, abs=1.0)


def test_a_worker_that_stopped_reporting_goes_stale(stack):
    import asyncio

    client, container, clock = stack
    asyncio.run(_beat(container))

    clock.set(NOW + timedelta(seconds=STALE_AFTER_SECONDS + 1))
    [row] = client.get("/api/workers").json()

    assert row["stale"] is True
    assert row["seconds_since_seen"] > STALE_AFTER_SECONDS


def test_readiness_fails_when_no_worker_has_ever_reported(stack):
    client, _container, _clock = stack

    check = next(
        c for c in client.get("/api/readiness").json()["checks"] if c["name"] == "workers"
    )

    assert check["status"] == "fail"
    assert "orchestrate worker start" in check["detail"]


def test_readiness_only_warns_when_a_known_worker_goes_quiet(stack):
    """A restart is normal. Calling it a failure trains an operator to ignore
    the check — the opposite of what a readiness surface is for."""
    import asyncio

    client, container, clock = stack
    asyncio.run(_beat(container))
    clock.set(NOW + timedelta(seconds=STALE_AFTER_SECONDS + 1))

    body = client.get("/api/readiness").json()
    check = next(c for c in body["checks"] if c["name"] == "workers")

    assert check["status"] == "warn"
    # Not `body["ok"] is True`: this fixture is a deliberately empty install, so
    # the catalog check fails for its own good reason. What matters here is that
    # a worker going quiet is not one of the things dragging readiness down.
    failing = {c["name"] for c in body["checks"] if c["status"] == "fail"}
    assert "workers" not in failing
