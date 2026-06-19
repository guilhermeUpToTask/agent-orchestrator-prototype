"""Integration tests for the file -> SQLite config importer (Phase 2)."""
from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from src.app.services.project_service import ProjectService
from src.app.services.registry_service import RegistryService
from src.infra.db.active_project import SqliteActiveProject
from src.infra.db.bootstrap import config_db
from src.infra.db.config_store import SqliteConfigStore
from src.infra.db.importer import import_config
from src.infra.db.secret_store import SqliteSecretStore


@pytest.fixture
def wired(tmp_path):
    # Lay out a legacy file tree under tmp_path.
    proj_dir = tmp_path / "projects" / "My App"
    (proj_dir / "agents").mkdir(parents=True)
    (proj_dir / "project.json").write_text(
        json.dumps({"source_repo_url": "git@x:y.git", "github_base_branch": "develop"})
    )
    (proj_dir / "agents" / "registry.json").write_text(
        json.dumps(
            {
                "worker-1": {
                    "name": "Worker One",
                    "runtime_type": "claude",
                    "runtime_config": {"model": "claude-opus-4-8"},
                    "capabilities": ["code:backend"],
                }
            }
        )
    )

    engine, sf = config_db(tmp_path)
    config = SqliteConfigStore(sf)
    secrets = SqliteSecretStore(sf, Fernet.generate_key())
    active = SqliteActiveProject(sf)
    return {
        "home": tmp_path,
        "config": config,
        "project_service": ProjectService(config, secrets, active),
        "registry_service": RegistryService(config, secrets),
        "env": {"ANTHROPIC_API_KEY": "sk-ant", "GITHUB_TOKEN": "ghp_x"},
    }


def _run(wired):
    return import_config(
        orchestrator_home=wired["home"],
        config_store=wired["config"],
        project_service=wired["project_service"],
        registry_service=wired["registry_service"],
        env=wired["env"],
    )


def test_import_creates_entities(wired) -> None:
    report = _run(wired)
    assert "anthropic" in report.providers_created
    assert "my-app" in report.projects_created
    assert "worker-1" in report.agents_created

    config = wired["config"]
    proj = config.get_project("my-app")
    assert proj.default_branch == "develop"
    assert proj.github_secret_ref is not None
    agent = config.get_agent("worker-1")
    assert agent.provider_id == "anthropic"
    assert agent.model_id == "claude-opus-4-8"


def test_import_is_idempotent(wired) -> None:
    first = _run(wired)
    second = _run(wired)
    assert first.projects_created == ["my-app"]
    assert second.projects_created == []
    assert second.providers_created == []
    assert second.agents_created == []
    # exactly one project, one provider, one agent — no duplicates
    config = wired["config"]
    assert len(config.list_projects()) == 1
    assert len(config.list_providers()) == 1
    assert len(config.list_agents()) == 1


def test_import_skips_agent_without_provider_key(tmp_path) -> None:
    proj_dir = tmp_path / "projects" / "p"
    (proj_dir / "agents").mkdir(parents=True)
    (proj_dir / "agents" / "registry.json").write_text(
        json.dumps(
            {
                "g1": {
                    "name": "Gem",
                    "runtime_type": "gemini",
                    "runtime_config": {"model": "gemini-x"},
                }
            }
        )
    )
    engine, sf = config_db(tmp_path)
    config = SqliteConfigStore(sf)
    secrets = SqliteSecretStore(sf, Fernet.generate_key())
    active = SqliteActiveProject(sf)
    report = import_config(
        orchestrator_home=tmp_path,
        config_store=config,
        project_service=ProjectService(config, secrets, active),
        registry_service=RegistryService(config, secrets),
        env={},  # no GEMINI_API_KEY -> provider not imported -> agent skipped
    )
    assert "agent:g1" in report.skipped
    assert config.get_agent("g1") is None
