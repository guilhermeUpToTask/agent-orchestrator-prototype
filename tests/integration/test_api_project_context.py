"""
tests/integration/test_api_project_context.py — API project context resolution.

Boots the real FastAPI app (no mock container) against a temporary
ORCHESTRATOR_HOME and verifies:
  - the API starts and serves /health even with no project configured
  - /project/context resolves the active project from .orchestrator/config.json
  - switching project_name in config.json is picked up without a restart
  - unresolved project context yields a clear 400, not a 500
  - startup does not crash in dry-run or real mode
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app


@pytest.fixture()
def orchestrator_home(tmp_path, monkeypatch):
    """Point the settings stack at an isolated orchestrator home."""
    home = tmp_path / ".orchestrator"
    home.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("AGENT_MODE", "dry-run")
    monkeypatch.delenv("PROJECT_NAME", raising=False)
    return home


def _write_config(home, **data) -> None:
    (home / "config.json").write_text(json.dumps(data), encoding="utf-8")


def test_health_works_without_project(orchestrator_home):
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_unresolved_project_returns_400(orchestrator_home):
    client = TestClient(create_app())
    resp = client.get("/api/project/context")
    assert resp.status_code == 400
    assert "No project configured" in resp.json()["detail"]


def test_context_resolves_active_project_from_config(orchestrator_home):
    _write_config(orchestrator_home, project_name="proj-a")
    client = TestClient(create_app())
    resp = client.get("/api/project/context")
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_name"] == "proj-a"
    assert body["mode"] == "dry-run"


def test_context_follows_config_change_without_restart(orchestrator_home):
    _write_config(orchestrator_home, project_name="proj-a")
    client = TestClient(create_app())
    assert client.get("/api/project/context").json()["project_name"] == "proj-a"

    _write_config(orchestrator_home, project_name="proj-b")
    assert client.get("/api/project/context").json()["project_name"] == "proj-b"

    # Removing the project entirely degrades to a 400, not a stale answer.
    _write_config(orchestrator_home)
    assert client.get("/api/project/context").status_code == 400


def test_plan_endpoint_scoped_to_active_project(orchestrator_home):
    _write_config(orchestrator_home, project_name="proj-a")
    client = TestClient(create_app())
    resp = client.get("/api/plan")
    assert resp.status_code == 200
    assert resp.json()["status"] == "discovery"


def test_startup_does_not_crash_in_real_mode(orchestrator_home, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "real")
    _write_config(orchestrator_home, project_name="proj-a")
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
    body = client.get("/api/project/context").json()
    assert body == {"project_name": "proj-a", "mode": "real"}
