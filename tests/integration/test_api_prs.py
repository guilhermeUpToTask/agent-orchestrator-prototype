"""API tests for the PR-window router (Phase 6), backed by LocalGitForge."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.infra.forge.local_git import LocalGitForge


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    from git import Repo

    repo = Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "T").release()
    repo.config_writer().set_value("user", "email", "t@x").release()
    (tmp_path / "f.txt").write_text("x")
    repo.index.add(["f.txt"])
    repo.index.commit("init")

    container = MagicMock()
    container.git_forge = LocalGitForge(str(tmp_path))
    return TestClient(create_app(container=container))


def test_commit_graph_endpoint(client) -> None:
    r = client.get("/api/projects/p1/commit-graph")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_git"
    assert len(body["nodes"][0]["sha"]) == 40


def test_capabilities_endpoint(client) -> None:
    r = client.get("/api/projects/p1/forge-capabilities")
    assert r.status_code == 200
    assert r.json()["supports_prs"] is False


def test_prs_empty_on_local_git(client) -> None:
    r = client.get("/api/projects/p1/prs")
    assert r.status_code == 200
    assert r.json() == []


def test_pending_reviews_route_not_shadowed_by_number(client) -> None:
    # /prs/pending-reviews must resolve to the pending-reviews handler, not
    # /prs/{number}.
    r = client.get("/api/prs/pending-reviews", params={"reviewer": "alice"})
    assert r.status_code == 200
    assert r.json() == []
