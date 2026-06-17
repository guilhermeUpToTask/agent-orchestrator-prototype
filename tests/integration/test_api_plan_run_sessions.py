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
        from src.domain.aggregates.project_plan import Phase, PhaseStatus
        from src.domain.value_objects.architecture_roadmap import ArchitectureRoadmap
        from src.domain.value_objects.goal import GoalSpec

        decision = SimpleNamespace(id="d1", domain="backend", feature_tag="api")
        phase = Phase(
            index=0,
            name="Foundation",
            goal="Stand up the base",
            goal_names=["g1"],
            status=PhaseStatus.PLANNED,
            lessons="",
            exit_criteria="done",
        )
        roadmap = ArchitectureRoadmap(
            phases=[phase],
            decisions=[decision],
            goal_specs={"g1": GoalSpec(name="g1", description="Build goal one.", tasks=[])},
        )

        def fake_run(**kwargs):
            return SimpleNamespace(
                failure_reason=None,
                roadmap=roadmap,
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
                {
                    "index": 0,
                    "name": "Foundation",
                    "goal_names": ["g1"],
                    "goals": [{"name": "g1", "description": "Build goal one."}],
                }
            ]

    def test_failure_reason_marks_session_failed(self, fresh_registry):
        def fake_run(**kwargs):
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

        def fake_run(**kwargs):
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


class TestArchitectureCancel:
    """POST /architecture/cancel requests a cooperative stop of the live run.

    Guards the interrupt plumbing end-to-end: the API sets the session's
    cancel flag, the (faked) orchestrator observes it via cancel_check and
    returns, and the background thread finalizes the session.
    """

    def _container(self, run_architecture):
        container = MagicMock()
        container.planner_orchestrator.run_architecture.side_effect = run_architecture
        return container

    def test_cancel_sets_flag_and_session_finalizes(self, fresh_registry):
        started = threading.Event()

        def fake_run(cancel_check=None, **kwargs):
            started.set()
            while not (cancel_check and cancel_check()):
                time.sleep(0.01)
            return SimpleNamespace(
                failure_reason=None, pending_decisions=[], pending_phases=[]
            )

        client = _client(self._container(fake_run))
        with client:
            sid = client.post("/api/plan/architecture/run").json()["session_id"]
            assert started.wait(timeout=5)

            assert client.post("/api/plan/architecture/cancel").status_code == 200

            session = _poll_session(fresh_registry, sid, {"done", "failed"})
            assert session.status == "done"

    def test_cancel_without_active_session_returns_404(self, fresh_registry):
        client = _client(self._container(lambda **kw: None))
        with client:
            assert client.post("/api/plan/architecture/cancel").status_code == 404


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


def _roadmap_run():
    """A fake run_architecture that produces one decision + one phase."""
    from src.domain.aggregates.project_plan import Phase, PhaseStatus
    from src.domain.value_objects.architecture_roadmap import ArchitectureRoadmap
    from src.domain.value_objects.goal import GoalSpec

    decision = SimpleNamespace(id="d1", domain="backend", feature_tag="api")
    phase = Phase(
        index=0,
        name="Foundation",
        goal="Stand up the base",
        goal_names=["g1"],
        status=PhaseStatus.PLANNED,
        lessons="",
        exit_criteria="done",
    )
    roadmap = ArchitectureRoadmap(
        phases=[phase],
        decisions=[decision],
        goal_specs={"g1": GoalSpec(name="g1", description="Build goal one.", tasks=[])},
    )

    def fake_run(**kwargs):
        return SimpleNamespace(
            failure_reason=None,
            roadmap=roadmap,
            pending_decisions=[decision],
            pending_phases=[phase],
        )

    return fake_run


class TestArchitectureStatus:
    """GET /architecture/status — reload-resilient readiness of the run."""

    def test_status_none_when_no_session(self, fresh_registry):
        client = _client(MagicMock())
        with client:
            r = client.get("/api/plan/architecture/status")
            assert r.status_code == 200
            assert r.json()["state"] == "none"

    def test_status_running_then_completed(self, fresh_registry):
        gate = threading.Event()

        def fake_run(**kwargs):
            gate.wait(timeout=5)
            return _roadmap_run()(**kwargs)

        container = MagicMock()
        container.planner_orchestrator.run_architecture.side_effect = fake_run
        client = _client(container)
        with client:
            client.post("/api/plan/architecture/run")
            assert client.get("/api/plan/architecture/status").json()["state"] == "running"
            gate.set()
            sid = _wait_status(client, "completed")
            body = client.get("/api/plan/architecture/status").json()
            assert body["state"] == "completed"
            assert body["decisions"] == [{"id": "d1", "domain": "backend", "feature_tag": "api"}]
            assert body["phases"][0]["name"] == "Foundation"
            assert sid is not None


class TestApproveBriefAutostart:
    """POST /approve-brief auto-launches the architecture session."""

    def test_approve_brief_autostarts_architecture_run(self, fresh_registry):
        from src.domain.aggregates.project_plan import ProjectPlanStatus

        container = MagicMock()
        container.planner_orchestrator.approve_brief.return_value = SimpleNamespace(
            status=ProjectPlanStatus.ARCHITECTURE, vision="Ship it"
        )
        container.planner_orchestrator.run_architecture.side_effect = _roadmap_run()

        client = _client(container)
        with client:
            r = client.post("/api/plan/approve-brief")
            assert r.status_code == 200
            assert r.json()["plan_status"] == "architecture"
            # The session was launched without a separate /architecture/run call.
            _wait_status(client, "completed")
            assert fresh_registry.latest("architecture") is not None

    def test_approve_brief_in_discovery_does_not_autostart(self, fresh_registry):
        from src.domain.aggregates.project_plan import ProjectPlanStatus

        container = MagicMock()
        # Non-architecture transition (shouldn't happen, but guard the condition).
        container.planner_orchestrator.approve_brief.return_value = SimpleNamespace(
            status=ProjectPlanStatus.DISCOVERY, vision="V"
        )
        client = _client(container)
        with client:
            assert client.post("/api/plan/approve-brief").status_code == 200
            assert fresh_registry.latest("architecture") is None
            container.planner_orchestrator.run_architecture.assert_not_called()


def _wait_status(client, target: str, timeout: float = 5.0) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get("/api/plan/architecture/status").json()
        if body["state"] == target:
            return body["session_id"]
        time.sleep(0.02)
    raise AssertionError(f"architecture status never reached {target}")
