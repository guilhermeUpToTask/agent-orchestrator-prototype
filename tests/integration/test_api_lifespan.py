"""
tests/integration/test_api_lifespan.py — embedded coordinator lifecycle.

The production API hosts the task manager, goal orchestrator and reconciler
as lifespan threads. These tests run that path against a real dry-run
container (in-memory adapters) and assert clean start and stop.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.infra.container import AppContainer
from src.infra.settings import SettingsService


def _write_dummy_spec(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "lifespan-test"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "project_spec.yaml").write_text(
        """
meta:
  name: lifespan-test
  version: 0.1.0
objective:
  description: test
  domain: test
"""
    )


@pytest.fixture
def dry_run_container(tmp_path):
    _write_dummy_spec(tmp_path)
    ctx = SettingsService.for_testing(
        orchestrator_home=tmp_path,
        project_name="lifespan-test",
        mode="dry-run",
        github_token="fake-token",
        anthropic_api_key="fake-key",
    )
    return AppContainer(ctx)


def test_lifespan_starts_and_stops_coordinator_threads(dry_run_container, monkeypatch):
    from src.api import server as server_mod

    monkeypatch.delenv("ORCHESTRATOR_EMBED_COORDINATORS", raising=False)
    monkeypatch.setenv("RECONCILER_INTERVAL", "60")
    # Production path (container=None) with the provider pinned to our
    # hermetic dry-run container instead of reading .orchestrator/config.json.
    monkeypatch.setattr(
        server_mod, "DynamicContainerProvider", lambda: (lambda: dry_run_container)
    )

    app = server_mod.create_app()
    with TestClient(app) as client:
        state = server_mod._COORDINATOR_STATE
        assert state, "coordinators should start on lifespan startup"
        assert {t.name for t in state["threads"]} == {
            "task-manager",
            "goal-orchestrator",
            "reconciler",
        }
        assert all(t.is_alive() for t in state["threads"])

        assert client.get("/health").status_code == 200

    # Lifespan shutdown stopped everything and cleared the registry.
    assert not server_mod._COORDINATOR_STATE


def test_lifespan_kill_switch_disables_coordinators(dry_run_container, monkeypatch):
    from src.api import server as server_mod

    monkeypatch.setenv("ORCHESTRATOR_EMBED_COORDINATORS", "0")
    monkeypatch.setattr(
        server_mod, "DynamicContainerProvider", lambda: (lambda: dry_run_container)
    )

    app = server_mod.create_app()
    with TestClient(app) as client:
        assert not server_mod._COORDINATOR_STATE
        assert client.get("/health").status_code == 200


def test_injected_container_apps_never_start_coordinators(dry_run_container):
    from src.api import server as server_mod

    app = server_mod.create_app(container=dry_run_container)
    with TestClient(app) as client:
        assert not server_mod._COORDINATOR_STATE
        assert client.get("/health").status_code == 200
