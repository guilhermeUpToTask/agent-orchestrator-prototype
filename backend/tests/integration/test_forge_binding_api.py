"""Binding a GitHub forge to a project, over the real API.

The token is verified against the exact repository BEFORE anything is stored,
so a credential that cannot open a pull request fails here rather than at a
publication gate at the end of a cycle.
"""

from __future__ import annotations

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from praxis_orchestrator.api import dependencies
from praxis_orchestrator.api.server import create_app
from praxis_orchestrator.infra.container import AppContainer
from praxis_orchestrator.infra.db.secret_ref import SecretRef
from praxis_orchestrator.infra.db.tables import Base
from praxis_orchestrator.infra.forge.github import GitHubForge
from praxis_orchestrator.infra.forge.no_forge import NoForge

pytestmark = pytest.mark.integration


@pytest.fixture
def container(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("PRAXIS_API_TOKEN", raising=False)
    made = AppContainer(orchestrator_home=tmp_path / "home")
    Base.metadata.create_all(made.engine)
    yield made
    dependencies.set_container(None)  # type: ignore[arg-type]


@pytest.fixture
def client(container):
    with TestClient(create_app(container)) as test_client:
        yield test_client


def _stub_github(monkeypatch, *, push: bool = True):
    """Point verify_github_token's client at a scripted transport."""
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "full_name": "acme/widgets",
                "default_branch": "main",
                "permissions": {"push": push},
            },
        )

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("praxis_orchestrator.infra.forge.github.httpx.Client", patched)


def _project(client) -> str:
    return client.post("/api/projects", json={"name": "p", "repo_url": None}).json()["id"]


def test_no_binding_reads_as_null(client):
    project_id = _project(client)

    response = client.get(f"/api/projects/{project_id}/forge")

    assert response.status_code == 200
    assert response.json() is None


def test_binding_a_verified_token_stores_it_encrypted(client, container, monkeypatch):
    _stub_github(monkeypatch)
    project_id = _project(client)

    response = client.put(
        f"/api/projects/{project_id}/forge",
        json={"repository": "acme/widgets", "token": "ghp_secret"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "provider": "github",
        "repository": "acme/widgets",
        "default_branch": "main",
    }
    # The token is retrievable only through the single decryption point.
    stored = container.secret_store.resolve(SecretRef.for_forge(project_id))
    assert stored.get_secret_value() == "ghp_secret"


def test_no_route_ever_echoes_the_token(client, monkeypatch):
    _stub_github(monkeypatch)
    project_id = _project(client)
    client.put(
        f"/api/projects/{project_id}/forge",
        json={"repository": "acme/widgets", "token": "ghp_secret"},
    )

    read = client.get(f"/api/projects/{project_id}/forge")

    assert "ghp_secret" not in read.text


def test_a_token_that_cannot_push_is_refused_and_stores_nothing(
    client, container, monkeypatch
):
    """Read access is not enough to open a pull request. Finding that out at
    the publication gate is exactly what this check prevents."""
    _stub_github(monkeypatch, push=False)
    project_id = _project(client)

    response = client.put(
        f"/api/projects/{project_id}/forge",
        json={"repository": "acme/widgets", "token": "ghp_readonly"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FORGE_AUTH_FAILED"
    assert not container.secret_store.exists(SecretRef.for_forge(project_id))
    assert client.get(f"/api/projects/{project_id}/forge").json() is None


def test_deleting_the_binding_removes_the_token_too(client, container, monkeypatch):
    _stub_github(monkeypatch)
    project_id = _project(client)
    client.put(
        f"/api/projects/{project_id}/forge",
        json={"repository": "acme/widgets", "token": "ghp_secret"},
    )

    response = client.delete(f"/api/projects/{project_id}/forge")

    assert response.status_code == 204
    assert client.get(f"/api/projects/{project_id}/forge").json() is None
    assert not container.secret_store.exists(SecretRef.for_forge(project_id))


def test_the_container_resolves_the_bound_forge_and_falls_back_otherwise(
    client, container, monkeypatch
):
    """`forge_for` re-reads per call, so a binding written in Settings lands on
    the next publication rather than the next worker restart."""
    _stub_github(monkeypatch)
    project_id = _project(client)

    assert isinstance(container.forge_for(project_id), NoForge)

    client.put(
        f"/api/projects/{project_id}/forge",
        json={"repository": "acme/widgets", "token": "ghp_secret"},
    )

    assert isinstance(container.forge_for(project_id), GitHubForge)
