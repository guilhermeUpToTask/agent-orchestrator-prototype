"""The thin API over TestClient: the plan lifecycle through HTTP, the error->
HTTP mapping table, reference-data CRUD with the no-plaintext secrets rule,
and two-tier config."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api import dependencies
from src.api.server import create_app
from src.infra.container import AppContainer
from src.infra.db.tables import Base

pytestmark = pytest.mark.integration


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "dry-run")
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    app = create_app(container)
    with TestClient(app) as test_client:
        yield test_client
    dependencies.set_container(None)  # type: ignore[arg-type]


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_plan_lifecycle_over_http(client):
    # create (idempotent on the Idempotency-Key header)
    created = client.post(
        "/api/plans",
        json={"brief": "goal: G1\ntask: t one"},
        headers={"Idempotency-Key": "req-1"},
    )
    assert created.status_code == 201
    plan_id = created.json()["plan_id"]
    again = client.post(
        "/api/plans",
        json={"brief": "goal: G1\ntask: t one"},
        headers={"Idempotency-Key": "req-1"},
    )
    assert again.json()["plan_id"] == plan_id

    # inspect
    fetched = client.get(f"/api/plans/{plan_id}")
    assert fetched.status_code == 200
    assert fetched.json()["phase"] == "discovery"
    assert client.get("/api/plans").status_code == 200

    # multi-turn discovery: an ask-turn replies without committing
    ask = client.post(
        f"/api/plans/{plan_id}/discovery/message",
        json={"message": "ask: which database should we use?"},
    )
    assert ask.status_code == 200
    assert ask.json() == {
        "reply": "which database should we use?",
        "committed": False,
        "phase": "discovery",
    }
    assert client.get(f"/api/plans/{plan_id}").json()["phase"] == "discovery"

    # the commit turn -> ARCHITECTURE
    turn = client.post(f"/api/plans/{plan_id}/discovery/message", json={"message": ""})
    assert turn.status_code == 200
    body = turn.json()
    assert body["committed"] is True
    assert body["phase"] == "architecture"
    assert client.get(f"/api/plans/{plan_id}").json()["phase"] == "architecture"

    # chat history: user/assistant alternation, insertion order, commit meta
    history = client.get(f"/api/plans/{plan_id}/chat").json()
    assert [(m["role"], m["meta"].get("committed")) for m in history] == [
        ("user", None),
        ("assistant", False),
        ("user", None),
        ("assistant", True),
    ]
    assert client.get("/api/plans/ghost/chat").status_code == 404


def test_error_mapping_table(client):
    # 404 PLAN_NOT_FOUND
    missing = client.get("/api/plans/ghost")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PLAN_NOT_FOUND"
    assert missing.json()["error"]["request_id"]

    # 422 EMPTY_PLAN (birth invariant)
    empty = client.post("/api/plans", json={"brief": "   "})
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "EMPTY_PLAN"

    # 422 INVALID_TRANSITION (approve a plan that isn't at the gate)
    plan_id = client.post("/api/plans", json={"brief": "b"}).json()["plan_id"]
    bad_approve = client.post(f"/api/plans/{plan_id}/approve")
    assert bad_approve.status_code == 422
    assert bad_approve.json()["error"]["code"] == "INVALID_TRANSITION"

    # 422 UNKNOWN_CAPABILITY via the edit boundary — but on a goal that exists
    # requires a plan with goals; simpler: 404 CAPABILITY_NOT_FOUND
    missing_cap = client.delete("/api/capabilities/ghost")
    assert missing_cap.status_code == 404
    assert missing_cap.json()["error"]["code"] == "CAPABILITY_NOT_FOUND"

    # 409 ENTITY_ALREADY_EXISTS
    cap = {"id": "c1", "name": "c", "description": "", "tools": []}
    assert client.post("/api/capabilities", json=cap).status_code == 201
    dup = client.post("/api/capabilities", json=cap)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "ENTITY_ALREADY_EXISTS"


def test_provider_secrets_never_echoed_or_stored_plaintext(client, tmp_path):
    created = client.post(
        "/api/providers",
        json={"name": "P", "base_url": "https://api", "api_key": "sk-super-secret"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["api_key_ref"].startswith("secret://provider/")
    assert "sk-super-secret" not in created.text

    listing = client.get("/api/providers")
    assert "sk-super-secret" not in listing.text

    # at rest: only ciphertext
    container = dependencies.get_container()
    with container.session_factory() as s:
        rows = s.execute(text("SELECT ciphertext FROM secrets")).all()
    assert rows and all("sk-super-secret" not in r[0] for r in rows)


def test_agents_and_default_marker_over_http(client):
    cap = {"id": "backend", "name": "backend", "description": "", "tools": []}
    client.post("/api/capabilities", json=cap)
    agent = client.post(
        "/api/agents",
        json={
            "name": "A",
            "role": "implementer",
            "model_role": "smart",
            "capability_ids": ["backend"],
        },
    )
    assert agent.status_code == 201
    agent_id = agent.json()["id"]
    assert agent.json()["capabilities"][0]["id"] == "backend"
    assert client.post(f"/api/agents/{agent_id}/default").status_code == 204


def test_two_tier_config_over_http(client):
    assert (
        client.put(
            "/api/config/orchestrator/poll_seconds", json={"value": "2"}
        ).status_code
        == 204
    )
    assert client.put(
        "/api/config/proj-1/framework", json={"value": "fastapi"}
    ).status_code == 204
    assert client.get("/api/config/orchestrator").json() == {"poll_seconds": "2"}
    assert client.get("/api/config/proj-1").json() == {"framework": "fastapi"}


def test_control_plane_token_guard(client, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "sekrit")
    denied = client.get("/api/providers")
    assert denied.status_code == 401
    allowed = client.get(
        "/api/providers", headers={"Authorization": "Bearer sekrit"}
    )
    assert allowed.status_code == 200
