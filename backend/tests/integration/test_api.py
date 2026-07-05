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


def test_model_rename_over_http(client):
    provider_id = client.post(
        "/api/providers",
        json={"name": "P", "base_url": "https://api", "api_key": "sk-1"},
    ).json()["id"]
    model = client.post(
        f"/api/providers/{provider_id}/models", json={"name": "gpt-old"}
    ).json()

    renamed = client.put(f"/api/models/{model['id']}", json={"name": "gpt-new"})
    assert renamed.status_code == 204
    listing = {m["id"]: m for m in client.get("/api/models").json()}
    assert listing[model["id"]]["name"] == "gpt-new"
    assert listing[model["id"]]["provider_id"] == provider_id

    ghost = client.put("/api/models/ghost", json={"name": "x"})
    assert ghost.status_code == 404
    assert ghost.json()["error"]["code"] == "MODEL_NOT_FOUND"


def test_reasoner_status_walk_over_http(client):
    # fresh DB: stub default, always valid
    fresh = client.get("/api/reasoner/status")
    assert fresh.status_code == 200
    assert fresh.json()["mode"] == "stub"
    assert fresh.json()["valid"] is True

    # llm mode without a provider: invalid with the actionable detail, still 200
    client.put("/api/config/orchestrator/reasoner.mode", json={"value": "llm"})
    unset = client.get("/api/reasoner/status").json()
    assert unset["valid"] is False
    assert "reasoner.provider_id" in unset["detail"]

    # wire a real provider + model over HTTP -> valid, names resolved
    provider = client.post(
        "/api/providers",
        json={"name": "OpenRouter", "base_url": "https://or", "api_key": "sk-1"},
    ).json()
    model = client.post(
        f"/api/providers/{provider['id']}/models", json={"name": "gpt-x"}
    ).json()
    client.put(
        "/api/config/orchestrator/reasoner.provider_id",
        json={"value": provider["id"]},
    )
    client.put(
        "/api/config/orchestrator/reasoner.model_id", json={"value": model["id"]}
    )
    wired = client.get("/api/reasoner/status").json()
    assert wired == {
        "mode": "llm",
        "valid": True,
        "detail": None,
        "provider_id": provider["id"],
        "provider_name": "OpenRouter",
        "model_id": model["id"],
        "model_name": "gpt-x",
    }

    # a model from a different provider: invalid cross-provider wiring
    other = client.post(
        "/api/providers",
        json={"name": "Other", "base_url": "https://o", "api_key": "sk-2"},
    ).json()
    stray = client.post(
        f"/api/providers/{other['id']}/models", json={"name": "m"}
    ).json()
    client.put(
        "/api/config/orchestrator/reasoner.model_id", json={"value": stray["id"]}
    )
    crossed = client.get("/api/reasoner/status").json()
    assert crossed["valid"] is False
    assert "belongs to provider" in crossed["detail"]


def test_runner_status_walk_over_http(client):
    # fresh DB: dry-run default, always valid; binaries reported informatively
    fresh = client.get("/api/runner/status")
    assert fresh.status_code == 200
    body = fresh.json()
    assert body["mode"] == "dry-run"
    assert body["valid"] is True
    assert {b["name"] for b in body["binaries"]} == {"git", "pi", "claude", "gemini"}
    assert body["agents"] == []

    # real mode with an unbound pi agent: invalid with the agent's detail
    agent = client.post(
        "/api/agents",
        json={"name": "A", "role": "implementer", "model_role": "smart"},
    ).json()
    client.put("/api/config/orchestrator/agent_runner.mode", json={"value": "real"})
    unbound = client.get("/api/runner/status").json()
    assert unbound["mode"] == "real"
    assert unbound["valid"] is False
    assert "no provider_id" in unbound["detail"]
    assert unbound["agents"][0]["runtime_type"] == "pi"

    # bind through the catalog (provider named after a pi backend) -> valid
    provider = client.post(
        "/api/providers",
        json={"name": "anthropic", "base_url": "https://a", "api_key": "sk-1"},
    ).json()
    model = client.post(
        f"/api/providers/{provider['id']}/models", json={"name": "sonnet"}
    ).json()
    assert (
        client.put(
            f"/api/agents/{agent['id']}",
            json={
                "name": "A",
                "role": "implementer",
                "model_role": "smart",
                "runtime_type": "claude",
                "provider_id": provider["id"],
                "model_id": model["id"],
            },
        ).status_code
        == 204
    )
    bound = client.get("/api/runner/status").json()
    assert bound["valid"] is True
    assert bound["agents"][0]["provider_name"] == "anthropic"
    assert bound["agents"][0]["model_name"] == "sonnet"

    # a bound provider/model is delete-guarded
    assert client.delete(f"/api/models/{model['id']}").status_code == 409
    assert client.delete(f"/api/providers/{provider['id']}").status_code == 409


def test_agent_runtime_write_validation_over_http(client):
    bad_runtime = client.post(
        "/api/agents",
        json={
            "name": "A",
            "role": "implementer",
            "model_role": "smart",
            "runtime_type": "cobol",
        },
    )
    assert bad_runtime.status_code == 422
    assert bad_runtime.json()["error"]["code"] == "AGENT_RUNNER_CONFIG_INVALID"

    ghost_provider = client.post(
        "/api/agents",
        json={
            "name": "A",
            "role": "implementer",
            "model_role": "smart",
            "provider_id": "ghost",
        },
    )
    assert ghost_provider.status_code == 404

    provider = client.post(
        "/api/providers",
        json={"name": "P1", "base_url": "https://p1", "api_key": "k1"},
    ).json()
    other = client.post(
        "/api/providers",
        json={"name": "P2", "base_url": "https://p2", "api_key": "k2"},
    ).json()
    stray = client.post(
        f"/api/providers/{other['id']}/models", json={"name": "m"}
    ).json()
    crossed = client.post(
        "/api/agents",
        json={
            "name": "A",
            "role": "implementer",
            "model_role": "smart",
            "provider_id": provider["id"],
            "model_id": stray["id"],
        },
    )
    assert crossed.status_code == 422
    assert "belongs to provider" in crossed.json()["error"]["message"]


def test_default_agent_read_over_http(client):
    assert client.get("/api/agents/default").json() == {"agent_id": None}
    agent_id = client.post(
        "/api/agents",
        json={"name": "A", "role": "implementer", "model_role": "smart"},
    ).json()["id"]
    client.post(f"/api/agents/{agent_id}/default")
    assert client.get("/api/agents/default").json() == {"agent_id": agent_id}


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
