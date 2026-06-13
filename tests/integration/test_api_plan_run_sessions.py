"""
tests/integration/test_api_plan_run_sessions.py — architecture / phase-review
run endpoints (the fix for the approve-architecture 409 dead-end).

These POST endpoints spawn the autonomous planner on a daemon thread, return
202 + a session id, and refuse a second concurrent run with 409. Completion
serializes the proposed decisions/phases onto the session result so the SSE
bridge and GatePanel have data to approve.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    """Each test gets an empty session registry, wired into the plan router."""
    from src.api import sessions as sessions_mod
    from src.api.routers import plan as plan_router

    registry = sessions_mod.SessionRegistry()
    monkeypatch.setattr(sessions_mod, "registry", registry)
    monkeypatch.setattr(plan_router, "registry", registry)
    return registry


def _client(container) -> TestClient:
    return TestClient(create_app(container=container))


def _poll_session(registry, sid: str, until: set[str], timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session = registry.get(sid)
        if session is not None and session.status in until:
            return session
        time.sleep(0.02)
    raise AssertionError(f"session {sid} never reached {until}")


class TestArchitectureRun:
    def _container(self, run_architecture):
        container = MagicMock()
        container.planner_orchestrator.run_architecture.side_effect = run_architecture
        return container

    def test_run_returns_202_and_serializes_decisions_and_phases(self, fresh_registry):
        decision = SimpleNamespace(id="d1", domain="backend", feature_tag="api")
        phase = SimpleNamespace(index=0, name="Foundation", goal_names=["g1"])

        def fake_run():
            return SimpleNamespace(
                failure_reason=None,
                pending_decisions=[decision],
                pending_phases=[phase],
            )

        client = _client(self._container(fake_run))
        with client:
            r = client.post("/api/plan/architecture/run")
            assert r.status_code == 202
            sid = r.json()["session_id"]

            session = _poll_session(fresh_registry, sid, {"done", "failed"})
            assert session.status == "done"
            assert session.result["decisions"] == [
                {"id": "d1", "domain": "backend", "feature_tag": "api"}
            ]
            assert session.result["phases"] == [
                {"index": 0, "name": "Foundation", "goal_names": ["g1"]}
            ]

    def test_failure_reason_marks_session_failed(self, fresh_registry):
        def fake_run():
            return SimpleNamespace(
                failure_reason="model does not support tool use",
                pending_decisions=[],
                pending_phases=[],
            )

        client = _client(self._container(fake_run))
        with client:
            sid = client.post("/api/plan/architecture/run").json()["session_id"]
            session = _poll_session(fresh_registry, sid, {"failed"})
            assert "tool use" in session.error

    def test_second_run_while_active_conflicts(self, fresh_registry):
        gate = threading.Event()

        def fake_run():
            gate.wait(timeout=5)
            return SimpleNamespace(
                failure_reason=None, pending_decisions=[], pending_phases=[]
            )

        client = _client(self._container(fake_run))
        with client:
            try:
                assert client.post("/api/plan/architecture/run").status_code == 202
                assert client.post("/api/plan/architecture/run").status_code == 409
            finally:
                gate.set()


class TestPhaseReviewRun:
    def _container(self, run_phase_review):
        container = MagicMock()
        container.planner_orchestrator.run_phase_review.side_effect = run_phase_review
        return container

    def test_run_returns_202_and_serializes_lessons_and_next_phase(self, fresh_registry):
        next_phase = SimpleNamespace(index=1, name="Core")
        decision = SimpleNamespace(id="d2", domain="frontend")

        def fake_run():
            return SimpleNamespace(
                failure_reason=None,
                lessons="keep tests green",
                next_phase_proposal=next_phase,
                pending_decisions=[decision],
            )

        client = _client(self._container(fake_run))
        with client:
            r = client.post("/api/plan/phase-review/run")
            assert r.status_code == 202
            sid = r.json()["session_id"]

            session = _poll_session(fresh_registry, sid, {"done", "failed"})
            assert session.status == "done"
            assert session.result["lessons"] == "keep tests green"
            assert session.result["next_phase"] == {"index": 1, "name": "Core"}
            assert session.result["decisions"] == [{"id": "d2", "domain": "frontend"}]
