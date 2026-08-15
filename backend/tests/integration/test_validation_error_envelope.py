"""A schema rejection is an error like any other — one envelope, no secrets.

Phase 10A found FastAPI's default `RequestValidationError` handler still in
place. It answers `{"detail": [...]}`, outside the envelope every other error
uses, and each entry carries `input` — **the value the caller sent**. For a
`missing` error that value is the whole submitted body, so a `POST
/api/providers` with a typo'd field name echoed the plaintext `api_key` back to
the caller, and `frontend/src/lib/toast.ts::errorDetail` — which reads a STRING
`detail` and falls through to the raw text on an ARRAY — rendered it into a
toast the operator could read.

Two properties are locked here: the envelope, and the silence about values.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from praxis_orchestrator.api import dependencies
from praxis_orchestrator.api.server import create_app
from praxis_orchestrator.infra.container import AppContainer
from praxis_orchestrator.infra.db.tables import Base

pytestmark = pytest.mark.integration

SECRET = "sk-live-NEVER-ECHO-THIS-abc123"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("PRAXIS_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    with TestClient(create_app(container)) as test_client:
        yield test_client
    dependencies.set_container(None)  # type: ignore[arg-type]


def test_a_missing_field_never_echoes_the_submitted_api_key(client):
    """The regression itself: `name` is absent, so pydantic's `input` for that
    error is the ENTIRE body — including the key."""
    response = client.post(
        "/api/providers",
        json={"base_url": "https://api.example.com", "api_key": SECRET},
    )

    assert response.status_code == 422
    assert SECRET not in response.text


def test_a_validation_error_uses_the_one_error_envelope(client):
    response = client.post("/api/providers", json={"base_url": "https://x.example"})

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}, "a 422 must not answer FastAPI's bare {'detail': [...]}"
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "request_id" in body["error"]


def test_the_message_names_the_offending_field(client):
    """Redacting values must not cost the operator the ability to fix it."""
    response = client.post("/api/providers", json={"base_url": "https://x.example"})

    message = response.json()["error"]["message"]
    assert "body.name" in message
    assert "Field required" in message


def test_a_bound_violation_reports_the_rule_and_not_the_value(client):
    """Pydantic's `msg` states the RULE; `input` states the value. Only the
    first is safe, and `-99` must not travel back with the key beside it."""
    response = client.post(
        "/api/providers",
        json={
            "name": "p",
            "base_url": "https://x.example",
            "api_key": SECRET,
            "max_inflight": -99,
        },
    )

    body = response.json()
    assert response.status_code == 422
    assert SECRET not in response.text
    assert "body.max_inflight" in body["error"]["message"]
    assert "greater than or equal to 1" in body["error"]["message"]


def test_a_domain_error_still_uses_the_same_envelope(client):
    """The envelope is shared, so a 404 and a 422 are parsed by one client path."""
    response = client.get("/api/plans/does-not-exist")

    assert response.status_code == 404
    assert set(response.json()) == {"error"}
