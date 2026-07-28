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

from src.api import dependencies
from src.api.server import create_app
from src.infra.container import AppContainer
from src.infra.db.tables import Base

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
