"""Integration tests for the control-plane API + observability (Phase 4)."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.api.server import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("AGENT_MODE", "dry-run")
    monkeypatch.setenv("ORCHESTRATOR_EMBED_COORDINATORS", "0")
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    from src.infra.container import AppContainer

    container = AppContainer.from_env()
    return TestClient(create_app(container=container))


SECRET = "sk-ant-SUPERSECRET"


def _seed_provider(client) -> None:
    r = client.post("/api/providers", json={
        "id": "anthropic", "kind": "anthropic", "api_key": SECRET,
    })
    assert r.status_code == 201, r.text


class TestProjects:
    def test_create_and_list(self, client) -> None:
        r = client.post("/api/projects", json={
            "name": "Web App", "repo_url": "git@x:y.git", "github_token": "ghp_x",
        })
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["id"] == "web-app"
        assert body["has_github_token"] is True
        assert "github_token" not in body  # write-only

        listed = client.get("/api/projects").json()
        assert any(p["id"] == "web-app" for p in listed)

    def test_activate(self, client) -> None:
        client.post("/api/projects", json={"name": "P", "repo_url": "r"})
        r = client.post("/api/projects/p/activate")
        assert r.status_code == 200

    def test_activate_missing_is_404_envelope(self, client) -> None:
        r = client.post("/api/projects/ghost/activate")
        assert r.status_code == 404
        err = r.json()["error"]
        assert err["code"] == "PROJECT_NOT_FOUND"
        assert err["request_id"]
        assert "Traceback" not in r.text


class TestProvidersAndAgents:
    def test_provider_model_agent_flow(self, client) -> None:
        _seed_provider(client)
        client.post("/api/providers/anthropic/models", json={"model_id": "claude-opus-4-8"})
        r = client.post("/api/agent-definitions", json={
            "id": "w1", "name": "Worker", "runtime_type": "claude",
            "provider_id": "anthropic", "model_id": "claude-opus-4-8",
            "capabilities": ["code:backend"],
        })
        assert r.status_code == 201, r.text
        agents = client.get("/api/agent-definitions").json()
        assert agents[0]["id"] == "w1"

    def test_unknown_model_is_400_envelope(self, client) -> None:
        _seed_provider(client)
        r = client.post("/api/agent-definitions", json={
            "id": "w1", "name": "W", "runtime_type": "claude",
            "provider_id": "anthropic", "model_id": "ghost",
        })
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "MODEL_NOT_REGISTERED"

    def test_delete_referenced_provider_is_409_envelope(self, client) -> None:
        _seed_provider(client)
        client.post("/api/providers/anthropic/models", json={"model_id": "m"})
        client.post("/api/agent-definitions", json={
            "id": "w1", "name": "W", "runtime_type": "claude",
            "provider_id": "anthropic", "model_id": "m",
        })
        r = client.delete("/api/providers/anthropic")
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "REFERENTIAL_CONSTRAINT"


class TestSecrets:
    def test_secret_write_only_and_masked_listing(self, client) -> None:
        _seed_provider(client)
        listed = client.get("/api/secrets").json()
        ref = next(s for s in listed if s["uri"] == "secret://provider/anthropic")
        assert ref["is_set"] is True
        # The plaintext must not appear anywhere in the listing.
        assert SECRET not in client.get("/api/secrets").text

    def test_store_secret(self, client) -> None:
        r = client.post("/api/secrets", json={
            "uri": "secret://provider/openai", "value": "sk-openai",
        })
        assert r.status_code == 201
        assert "sk-openai" not in r.text


class TestObservability:
    def test_request_id_header_present(self, client) -> None:
        r = client.get("/api/projects")
        assert r.headers.get("X-Request-ID")

    def test_inbound_request_id_propagated(self, client) -> None:
        r = client.get("/api/projects", headers={"X-Request-ID": "trace-123"})
        assert r.headers.get("X-Request-ID") == "trace-123"


class TestAuth:
    def test_token_required_when_configured(self, client, monkeypatch) -> None:
        monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "s3cret")
        r = client.get("/api/projects")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVALID_API_TOKEN"

    def test_token_accepted(self, client, monkeypatch) -> None:
        monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "s3cret")
        r = client.get("/api/projects", headers={"X-API-Token": "s3cret"})
        assert r.status_code == 200
