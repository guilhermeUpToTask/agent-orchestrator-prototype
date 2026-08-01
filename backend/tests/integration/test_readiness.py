"""One call for the setup checklist, and one per project.

Before this, "can this machine run a plan?" was assembled by hand from
/api/reasoner/status and /api/runner/status plus operator inference, and the
first symptom of an incomplete setup was a failed run.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from agent_orchestrator.api import dependencies
from agent_orchestrator.api.server import create_app
from agent_orchestrator.infra.container import AppContainer
from agent_orchestrator.infra.db.tables import Base

pytestmark = pytest.mark.integration


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path / "home")
    Base.metadata.create_all(container.engine)
    with TestClient(create_app(container)) as test_client:
        yield test_client
    dependencies.set_container(None)  # type: ignore[arg-type]


def test_readiness_names_every_check(client):
    body = client.get("/api/readiness").json()

    assert {check["name"] for check in body["checks"]} == {
        "reasoner", "runner", "binaries", "secrets", "catalog", "projects", "workers",
    }
    assert all(check["status"] in {"ok", "warn", "fail"} for check in body["checks"])


def test_an_empty_installation_is_not_ready(client):
    body = client.get("/api/readiness").json()

    assert body["ok"] is False
    catalog = next(c for c in body["checks"] if c["name"] == "catalog")
    assert catalog["status"] == "fail"


def test_readiness_never_returns_secret_material(client, tmp_path):
    client.post(
        "/api/providers",
        json={"name": "p", "base_url": "https://api.example.com", "api_key": "sk-secret-value"},
    )

    payload = json.dumps(client.get("/api/readiness").json())

    assert "sk-secret-value" not in payload


def _git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "checkout", "-B", "main"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return path


def test_project_readiness_reports_a_broken_binding(client, tmp_path):
    repo = _git_repo(tmp_path / "repo")
    project = client.post("/api/projects", json={"name": "P", "repo_url": str(repo)}).json()

    body = client.get(f"/api/projects/{project['id']}/readiness").json()
    assert body["binding"] == "local"
    assert body["is_git_repository"] is True
    assert body["problem"] is None

    # The directory disappears after a valid binding — the case write-time
    # validation cannot catch.
    subprocess.run(["rm", "-rf", str(repo)], check=True)

    body = client.get(f"/api/projects/{project['id']}/readiness").json()
    assert body["exists"] is False
    assert body["problem"] is not None


def test_a_scratch_project_says_so(client):
    project = client.post("/api/projects", json={"name": "P"}).json()

    body = client.get(f"/api/projects/{project['id']}/readiness").json()

    assert body["binding"] == "scratch"
    assert body["resolved_path"] is not None


def test_a_free_tier_install_without_a_master_key_is_not_reported_broken(
    tmp_path, monkeypatch
):
    """Tier 0 is stub + dry-run, and neither ever touches the secret store.

    Reporting `fail` for a missing master key there would tell an operator with
    a perfectly good free-tier install that it is broken — the exact false
    alarm the ok/warn/fail split exists to avoid.
    """
    monkeypatch.delenv("ORCHESTRATOR_MASTER_KEY", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path / "free")
    Base.metadata.create_all(container.engine)
    with TestClient(create_app(container)) as client:
        body = client.get("/api/readiness").json()
    dependencies.set_container(None)  # type: ignore[arg-type]

    secrets = next(check for check in body["checks"] if check["name"] == "secrets")
    assert secrets["status"] == "ok", secrets["detail"]
    assert "not needed" in secrets["detail"]


def test_project_readiness_reports_a_dirty_worktree(client, tmp_path):
    """Informational, not a verdict: attempts branch from a committed ref, but
    an operator about to start a 25-minute cycle wants to know."""
    repo = _git_repo(tmp_path / "dirty")
    project = client.post(
        "/api/projects", json={"name": "P", "repo_url": str(repo)}
    ).json()

    assert client.get(f"/api/projects/{project['id']}/readiness").json()["clean"] is True

    (repo / "scratch.txt").write_text("uncommitted")

    assert client.get(f"/api/projects/{project['id']}/readiness").json()["clean"] is False
