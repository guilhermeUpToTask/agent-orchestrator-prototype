"""
tests/integration/test_api_sessions.py — session-pattern discovery/refine.

Drives the 202 + session-id flow end to end with a stub planner running on
the real executor: questions surface via GET, answers flow back through the
session queue, completion carries the brief, and failures never brick the
next start (regression for the old never-reset _discovery_active flag).
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    """Each test gets an empty session registry."""
    from src.api import sessions as sessions_mod
    from src.api.routers import discovery as discovery_router
    from src.api.routers import refinement as refinement_router

    registry = sessions_mod.SessionRegistry()
    monkeypatch.setattr(sessions_mod, "registry", registry)
    monkeypatch.setattr(discovery_router, "registry", registry)
    monkeypatch.setattr(refinement_router, "registry", registry)
    return registry


def _client(container) -> TestClient:
    return TestClient(create_app(container=container))


def _poll(client, url: str, until: set[str], timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(url).json()
        if body.get("status") in until:
            return body
        time.sleep(0.02)
    raise AssertionError(f"{url} never reached {until}; last: {body}")


class TestDiscoverySessions:
    def _container_with_orchestrator(self, start_discovery):
        container = MagicMock()
        container.planner_orchestrator.start_discovery.side_effect = start_discovery
        return container

    def test_full_question_answer_completion_flow(self):
        brief = SimpleNamespace(model_dump=lambda: {"vision": "v1"})

        def fake_discovery(io_handler):
            answer = io_handler("What are we building?")
            assert answer == "a rocket"
            return SimpleNamespace(failure_reason=None, brief=brief)

        client = _client(self._container_with_orchestrator(fake_discovery))
        with client:
            started = client.post("/api/plan/discovery/start")
            assert started.status_code == 202
            sid = started.json()["session_id"]

            body = _poll(client, f"/api/plan/discovery/{sid}", {"waiting_input"})
            assert body["question"] == "What are we building?"

            answered = client.post(
                f"/api/plan/discovery/{sid}/message", json={"message": "a rocket"}
            )
            assert answered.status_code == 202

            body = _poll(client, f"/api/plan/discovery/{sid}", {"done", "failed"})
            assert body["status"] == "done"
            assert body["result"]["brief"] == {"vision": "v1"}

    def test_failed_session_does_not_block_next_start(self):
        calls = {"n": 0}

        def fake_discovery(io_handler):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("LLM exploded")
            return SimpleNamespace(failure_reason=None, brief=None)

        client = _client(self._container_with_orchestrator(fake_discovery))
        with client:
            first = client.post("/api/plan/discovery/start")
            sid = first.json()["session_id"]
            body = _poll(client, f"/api/plan/discovery/{sid}", {"failed"})
            assert "LLM exploded" in body["error"]

            # Regression: the old module-global flag stayed True forever here.
            second = client.post("/api/plan/discovery/start")
            assert second.status_code == 202
            sid2 = second.json()["session_id"]
            assert _poll(client, f"/api/plan/discovery/{sid2}", {"done"})["status"] == "done"

    def test_concurrent_start_conflicts(self):
        def fake_discovery(io_handler):
            io_handler("q?")  # parks waiting for an answer that never comes
            return SimpleNamespace(failure_reason=None, brief=None)

        client = _client(self._container_with_orchestrator(fake_discovery))
        with client:
            assert client.post("/api/plan/discovery/start").status_code == 202
            assert client.post("/api/plan/discovery/start").status_code == 409

    def test_message_to_unknown_session_is_404(self):
        client = _client(MagicMock())
        with client:
            r = client.post("/api/plan/discovery/nope/message", json={"message": "x"})
            assert r.status_code == 404

    def test_message_when_not_waiting_is_409(self):
        def fake_discovery(io_handler):
            return SimpleNamespace(failure_reason=None, brief=None)  # no questions

        client = _client(self._container_with_orchestrator(fake_discovery))
        with client:
            sid = client.post("/api/plan/discovery/start").json()["session_id"]
            _poll(client, f"/api/plan/discovery/{sid}", {"done"})
            r = client.post(f"/api/plan/discovery/{sid}/message", json={"message": "x"})
            assert r.status_code == 409


class TestRefineSessions:
    def _container_with_refine(self, execute):
        container = MagicMock()
        container.run_refinement_usecase.execute.side_effect = execute
        return container

    def test_refine_returns_202_and_result_via_session_get(self):
        def fake_execute(user_message, focused_node_id, focused_goal_id):
            return SimpleNamespace(
                session_id="planner-1",
                actions_taken=["added task t1"],
                succeeded=True,
                error=None,
            )

        client = _client(self._container_with_refine(fake_execute))
        with client:
            r = client.post("/api/plan/refine", json={"message": "add a task"})
            assert r.status_code == 202
            sid = r.json()["session_id"]

            body = _poll(client, f"/api/plan/sessions/{sid}", {"done", "failed"})
            assert body["status"] == "done"
            assert body["result"]["actions_taken"] == ["added task t1"]
            assert body["result"]["succeeded"] is True

    def test_refine_failure_is_reported_via_session(self):
        def fake_execute(**_kw):
            raise RuntimeError("context assembly failed")

        client = _client(self._container_with_refine(fake_execute))
        with client:
            sid = client.post("/api/plan/refine", json={"message": "x"}).json()["session_id"]
            body = _poll(client, f"/api/plan/sessions/{sid}", {"failed"})
            assert "context assembly failed" in body["error"]

    def test_unknown_session_get_is_404(self):
        client = _client(MagicMock())
        with client:
            assert client.get("/api/plan/sessions/nope").status_code == 404
