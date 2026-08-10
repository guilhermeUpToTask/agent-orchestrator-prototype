"""The guard is default-on, and proven over the whole route inventory.

The Phase 3 audit found `plans.router` and `events.router` unguarded, so 36 of
the 64 served operations answered unauthenticated callers while `security.py`
claimed the opposite. The defect was not two missing declarations — it was that
the guard was OPT-IN, so any router, including ones not yet written, was
unguarded by default. Parametrizing over `app.openapi()` rather than a
hand-written list is what makes a future router covered before it exists.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from agent_orchestrator.api import dependencies
from agent_orchestrator.api.server import create_app
from agent_orchestrator.infra.container import AppContainer
from agent_orchestrator.infra.db.tables import Base

pytestmark = pytest.mark.integration

# A liveness check that requires a secret cannot serve the setup checklist it
# exists for.
OPEN_OPERATIONS = {("GET", "/health")}

# `/api/events` is an endless stream: once the guard admits a request, the
# response never completes and `TestClient` blocks forever. It is guarded like
# everything else — asserted against a real socket in
# `test_sse_stream.py::test_the_event_stream_refuses_an_unauthenticated_reader`,
# which can close the connection itself.
STREAMING_OPERATIONS = {("GET", "/api/events")}

_PATH_PLACEHOLDERS = (
    "{plan_id}",
    "{attempt_id}",
    "{project_id}",
    "{provider_id}",
    "{model_id}",
    "{agent_id}",
    "{capability_id}",
    "{scope}",
    "{key}",
)


def _operations() -> list[tuple[str, str]]:
    paths = create_app().openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
        if method.upper() in {"GET", "POST", "PUT", "DELETE"}
    )


@pytest.fixture
def guarded_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "sekrit")
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    with TestClient(create_app(container)) as client:
        yield client
    dependencies.set_container(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(("method", "path"), _operations(), ids=lambda value: str(value))
def test_no_operation_answers_without_a_token(guarded_client, method, path):
    """No body is sent on purpose: FastAPI solves router-level dependencies
    before body validation, so a missing token must win over a missing body."""
    if (method, path) in OPEN_OPERATIONS:
        pytest.skip("deliberately open")
    if (method, path) in STREAMING_OPERATIONS:
        pytest.skip("endless stream — covered against a real socket in test_sse_stream.py")
    url = path
    for placeholder in _PATH_PLACEHOLDERS:
        url = url.replace(placeholder, "x")

    response = guarded_client.request(method, url)

    assert response.status_code == 401, (
        f"{method} {path} answered {response.status_code} without a token"
    )
    assert response.json()["error"]["code"] == "INVALID_API_TOKEN"


def test_health_stays_open(guarded_client):
    assert guarded_client.get("/health").status_code == 200


def test_a_wrong_query_token_is_refused_by_the_stream(guarded_client):
    """The stream's rejection boundary. A CORRECT token is not exercised here —
    the response would never complete — but against a real socket in
    test_sse_stream.py::test_the_guarded_stream_opens_with_a_query_token."""
    assert guarded_client.get("/api/events", params={"token": "wrong"}).status_code == 401


def test_no_other_route_accepts_a_query_token(guarded_client):
    """Confining the URL-borne token to the stream is the point: everywhere a
    header works, a header is required."""
    response = guarded_client.get("/api/providers", params={"token": "sekrit"})

    assert response.status_code == 401


# The parametrized sweep above cannot see these three. FastAPI mounts its own
# docs routes on the bare app with `include_in_schema=False`, so they are absent
# from `openapi()["paths"]` — the very inventory this file trusts — and no
# router dependency reaches them. Phase 10A found all three answering 200 with
# `ORCHESTRATOR_API_TOKEN` set, serving the whole control-plane schema to an
# anonymous caller. They are named explicitly because they cannot be discovered.
API_DOC_ROUTES = ("/api/openapi.json", "/api/docs", "/api/redoc")


@pytest.mark.parametrize("path", API_DOC_ROUTES)
def test_the_api_documentation_is_guarded(guarded_client, path):
    response = guarded_client.get(path)

    assert response.status_code == 401, f"{path} served documentation without a token"
    assert response.json()["error"]["code"] == "INVALID_API_TOKEN"


@pytest.mark.parametrize("path", API_DOC_ROUTES)
def test_the_api_documentation_opens_with_a_token(guarded_client, path):
    """Guarded, not removed — an authenticated operator still gets the docs."""
    response = guarded_client.get(path, headers={"X-API-Token": "sekrit"})

    assert response.status_code == 200


def test_the_schema_is_not_reachable_by_a_query_token(guarded_client):
    """The `?token=` mechanism stays confined to the SSE stream."""
    response = guarded_client.get("/api/openapi.json", params={"token": "sekrit"})

    assert response.status_code == 401
