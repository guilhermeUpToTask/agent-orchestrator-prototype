"""The packaged frontend, served by the API that owns its types.

Phase 6 ships ONE artifact: a wheel a user installs with `uvx`/`pipx`. The
built UI has to travel inside it, or "install to green Tier 0 using public docs
only" means installing a Python package and then separately fetching a tarball
from a GitHub release — two artifacts that can disagree about the API version
they were generated against, which is exactly what the exit criterion
"packaged frontend/backend agree on API version/types" forbids.

Serving it from the API also removes a second port and a CORS story from the
first-run path.

The bundle is OPTIONAL: a source checkout that has never run `npm run build`
must still start, because that is how every backend test and every fixture run
works today.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from praxis_orchestrator.api import dependencies
from praxis_orchestrator.api import frontend as frontend_module
from praxis_orchestrator.api.server import create_app
from praxis_orchestrator.infra.container import AppContainer
from praxis_orchestrator.infra.db.tables import Base

pytestmark = pytest.mark.integration


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """A stand-in for `npm run build` output."""
    directory = tmp_path / "static"
    (directory / "assets").mkdir(parents=True)
    (directory / "index.html").write_text("<!doctype html><title>Orchestrator</title>")
    (directory / "assets" / "app.js").write_text("console.log('ui')")
    monkeypatch.setattr(frontend_module, "bundle_dir", lambda: directory)
    return directory


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("PRAXIS_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path / "home")
    Base.metadata.create_all(container.engine)
    with TestClient(create_app(container)) as test_client:
        yield test_client
    dependencies.set_container(None)  # type: ignore[arg-type]


def test_the_ui_is_served_at_the_root_when_the_bundle_ships(bundle, client) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "<title>Orchestrator</title>" in response.text


def test_bundle_assets_are_served(bundle, client) -> None:
    assert client.get("/assets/app.js").status_code == 200


def test_a_client_side_route_falls_back_to_index(bundle, client) -> None:
    """`/plans/abc` exists only in the browser router; a deep link or a refresh
    must not 404."""
    response = client.get("/plans/abc/goals")

    assert response.status_code == 200
    assert "<title>Orchestrator</title>" in response.text


def test_the_api_still_wins_over_the_fallback(bundle, client) -> None:
    """The SPA fallback must never swallow the API: an unknown /api path is a
    404 the client can interpret, not an HTML page."""
    assert client.get("/api/plans").status_code == 200
    unknown = client.get("/api/there-is-no-such-route")
    assert unknown.status_code == 404
    assert "<title>" not in unknown.text


def test_health_still_wins_over_the_fallback(bundle, client) -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_docs_reaches_the_console_manual_not_swagger(bundle, client) -> None:
    """`/docs` is the console's own documentation.

    FastAPI puts Swagger there by default, and the reserved-prefix list used to
    name `docs` outright — so the console route was shadowed twice over and
    rendered the API explorer instead. Nothing failed: both are a 200 with
    HTML, which is why this needs a test that reads the title rather than the
    status code.
    """
    response = client.get("/docs")

    assert response.status_code == 200
    assert "<title>Orchestrator</title>" in response.text
    assert "swagger" not in response.text.lower()


def test_the_api_explorer_moved_under_api(bundle, client) -> None:
    assert client.get("/api/docs").status_code == 200
    assert client.get("/api/openapi.json").json()["info"]["title"]


def test_the_old_schema_location_404s_rather_than_serving_html(bundle, client) -> None:
    """A generator pointed at the old `/openapi.json` must fail legibly. An
    HTML page with a 200 is the worst possible answer for tooling."""
    response = client.get("/openapi.json")

    assert response.status_code == 404
    assert "<title>" not in response.text


def test_the_api_starts_without_a_bundle(tmp_path, monkeypatch) -> None:
    """A source checkout that never ran `npm run build` must still work — that
    is how the test suite and every fixture run operate."""
    monkeypatch.setattr(frontend_module, "bundle_dir", lambda: tmp_path / "absent")
    monkeypatch.delenv("PRAXIS_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path / "home")
    Base.metadata.create_all(container.engine)

    with TestClient(create_app(container)) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/").status_code == 404
    dependencies.set_container(None)  # type: ignore[arg-type]


def test_the_bundle_directory_is_inside_the_package() -> None:
    """Same property the migrations needed: an installed copy has no
    repository, so the path must be derived from the package."""
    import praxis_orchestrator

    assert frontend_module.bundle_dir().is_relative_to(Path(praxis_orchestrator.__file__).resolve().parent)
