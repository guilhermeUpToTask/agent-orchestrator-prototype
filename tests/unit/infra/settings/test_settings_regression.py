"""
tests/unit/infra/settings/test_settings_regression.py

Regression tests covering the settings package boundary.  These tests
exist to prevent the three critical failure modes that motivated the refactor:

  1. Secrets written to JSON files (github_token, api keys)
  2. Application code bypassing the settings boundary
  3. Path derivation inconsistencies

Each test class maps to one acceptance criterion from the refactor spec.
"""

from __future__ import annotations

import json

import pytest

from src.infra.settings import (
    GlobalConfigStore,
    MachineSettings,
    ProjectConfigStore,
    ProjectSettings,
    SecretSettings,
    SettingsContext,
    SettingsService,
)
from src.infra.project_paths import ProjectPaths


# ---------------------------------------------------------------------------
# 1. Secret exclusion — github_token must never reach disk
# ---------------------------------------------------------------------------


class TestSecretExclusionFromPersistence:
    """The most critical regression guard: no secret ever touches a JSON file."""

    def test_global_store_never_writes_github_token(self, tmp_path):
        store = GlobalConfigStore(home=tmp_path)
        store.save({"project_name": "p", "github_token": "ghp_LEAK"})
        on_disk = json.loads(store.config_path.read_text())
        assert "github_token" not in on_disk, "github_token must never be written to config.json"

    def test_project_store_never_writes_github_token(self, tmp_path):
        project_home = tmp_path / "projects" / "p"
        store = ProjectConfigStore(project_home)
        # ProjectSettings has no github_token field — verify the store
        # also strips it if somehow injected via raw dict at the filesystem level
        project_home.mkdir(parents=True)
        # Pre-seed a file with github_token to simulate a migrated legacy file
        existing = project_home / "project.json"
        existing.write_text(json.dumps({"github_owner": "acme", "github_token": "ghp_OLD"}))
        # Load then save — token must be stripped
        settings = store.load()
        store.save(settings)
        on_disk = json.loads(existing.read_text())
        assert "github_token" not in on_disk, "save() must strip github_token from project.json"

    def test_project_store_never_writes_api_keys(self, tmp_path):
        project_home = tmp_path / "projects" / "p"
        store = ProjectConfigStore(project_home)
        settings = ProjectSettings(github_owner="acme", github_repo="repo")
        store.save(settings)
        on_disk = json.loads((project_home / "project.json").read_text())
        for key in ("anthropic_api_key", "gemini_api_key", "openrouter_api_key", "github_token"):
            assert key not in on_disk

    def test_global_store_never_writes_any_api_key(self, tmp_path):
        store = GlobalConfigStore(home=tmp_path)
        store.save(
            {
                "project_name": "p",
                "anthropic_api_key": "sk-ant",
                "gemini_api_key": "gm-key",
                "openrouter_api_key": "or-key",
            }
        )
        on_disk = json.loads(store.config_path.read_text())
        for key in ("anthropic_api_key", "gemini_api_key", "openrouter_api_key"):
            assert key not in on_disk

    def test_secret_settings_never_exposes_values_in_repr(self):
        s = SecretSettings(
            anthropic_api_key="sk-ant-secret",
            github_token="ghp_secret",
        )
        text = repr(s) + str(s)
        assert "sk-ant-secret" not in text
        assert "ghp_secret" not in text


# ---------------------------------------------------------------------------
# 2. Loading defaults
# ---------------------------------------------------------------------------


class TestLoadingDefaults:
    def test_machine_defaults_without_any_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        for key in (
            "AGENT_MODE",
            "REDIS_URL",
            "TASK_TIMEOUT_SECONDS",
            "PROJECT_NAME",
            "AGENT_ID",
            "GITHUB_TOKEN",
            "ANTHROPIC_API_KEY",
        ):
            monkeypatch.delenv(key, raising=False)
        ctx = SettingsService(home=tmp_path).load()
        assert ctx.machine.mode == "dry-run"
        assert ctx.machine.redis_url == "redis://localhost:6379/0"
        assert ctx.machine.task_timeout == 600

    def test_project_defaults_without_project_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        ctx = SettingsService(home=tmp_path).load(project_name="new-proj")
        # project.json doesn't exist — should get defaults, not crash
        assert ctx.project.github_base_branch == "main"
        assert ctx.project.source_repo_url is None

    def test_secrets_default_to_empty_strings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        for key in ("GITHUB_TOKEN", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        ctx = SettingsService(home=tmp_path).load()
        assert ctx.secrets.github_token == ""
        assert ctx.secrets.anthropic_api_key == ""


# ---------------------------------------------------------------------------
# 3. Loading env secrets
# ---------------------------------------------------------------------------


class TestLoadingEnvSecrets:
    def test_github_token_from_env_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_from_env")
        ctx = SettingsService(home=tmp_path).load()
        assert ctx.secrets.github_token == "ghp_from_env"

    def test_anthropic_key_from_env_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fromenv")
        ctx = SettingsService(home=tmp_path).load()
        assert ctx.secrets.anthropic_api_key == "sk-ant-fromenv"

    def test_secrets_not_read_from_config_json(self, tmp_path, monkeypatch):
        """Secrets written into config.json by a previous (broken) version must be ignored."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Simulate a legacy file that accidentally contains a secret
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"project_name": "p", "anthropic_api_key": "sk-should-ignore"})
        )
        ctx = SettingsService(home=tmp_path).load()
        # The store strips it on load; secrets come from env only
        assert ctx.secrets.anthropic_api_key == ""


# ---------------------------------------------------------------------------
# 4. Loading global config
# ---------------------------------------------------------------------------


class TestLoadingGlobalConfig:
    def test_project_name_from_config_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.delenv("PROJECT_NAME", raising=False)  # <-- Shield from host

        store = GlobalConfigStore(home=tmp_path)
        store.save({"project_name": "from-file"})
        ctx = SettingsService(home=tmp_path).load()
        assert ctx.machine.project_name == "from-file"

    def test_redis_url_from_config_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.delenv("REDIS_URL", raising=False)  # <-- Shield from host

        store = GlobalConfigStore(home=tmp_path)
        store.save({"redis_url": "redis://from-file:6379/0"})
        ctx = SettingsService(home=tmp_path).load()
        assert ctx.machine.redis_url == "redis://from-file:6379/0"

    def test_explicit_override_beats_config_json_for_project_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.delenv("PROJECT_NAME", raising=False)

        store = GlobalConfigStore(home=tmp_path)
        store.save({"project_name": "from-file"})
        ctx = SettingsService(home=tmp_path).load(project_name="explicit-override")
        assert ctx.machine.project_name == "explicit-override"


# ---------------------------------------------------------------------------
# 5. Loading project config
# ---------------------------------------------------------------------------


class TestLoadingProjectConfig:
    def test_source_repo_url_from_project_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        project_home = tmp_path / "projects" / "myproj"
        ProjectConfigStore(project_home).save(
            ProjectSettings(source_repo_url="https://github.com/acme/repo")
        )
        ctx = SettingsService(home=tmp_path).load(project_name="myproj")
        assert ctx.project.source_repo_url == "https://github.com/acme/repo"

    def test_github_owner_repo_from_project_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        project_home = tmp_path / "projects" / "myproj"
        ProjectConfigStore(project_home).save(
            ProjectSettings(github_owner="acme", github_repo="widget")
        )
        ctx = SettingsService(home=tmp_path).load(project_name="myproj")
        assert ctx.project.github_owner == "acme"
        assert ctx.project.github_repo == "widget"


# ---------------------------------------------------------------------------
# 6. Path derivation
# ---------------------------------------------------------------------------


class TestPathDerivation:
    def test_all_paths_derived_from_home_and_project(self, tmp_path):
        paths = ProjectPaths.for_project(tmp_path, "myproj")
        base = tmp_path / "projects" / "myproj"
        assert paths.project_home == base
        assert paths.tasks_dir == base / "tasks"
        assert paths.goals_dir == base / "goals"
        assert paths.registry_path == base / "agents" / "registry.json"
        assert paths.workspace_dir == base / "workspaces"
        assert paths.logs_dir == base / "logs"
        assert paths.events_dir == base / "events"
        assert paths.repo_url == f"file://{base / 'repo'}"

    def test_paths_consistent_across_calls(self, tmp_path):
        """ProjectPaths is a pure computation — same inputs always yield same outputs."""
        p1 = ProjectPaths.for_project(tmp_path, "proj")
        p2 = ProjectPaths.for_project(tmp_path, "proj")
        assert p1 == p2

    def test_different_projects_get_different_paths(self, tmp_path):
        p_a = ProjectPaths.for_project(tmp_path, "proj-a")
        p_b = ProjectPaths.for_project(tmp_path, "proj-b")
        assert p_a.tasks_dir != p_b.tasks_dir

    def test_paths_not_persisted_in_config_json(self, tmp_path):
        store = GlobalConfigStore(home=tmp_path)
        store.save({"project_name": "p"})
        on_disk = json.loads(store.config_path.read_text())
        for path_key in ("tasks_dir", "logs_dir", "workspace_dir", "registry_path", "repo_url"):
            assert path_key not in on_disk, f"{path_key} must never be persisted"


# ---------------------------------------------------------------------------
# 7. Persistence boundaries
# ---------------------------------------------------------------------------


class TestPersistenceBoundaries:
    def test_save_machine_only_persists_allowed_keys(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        svc = SettingsService(home=tmp_path)
        svc.save_machine(project_name="p", redis_url="redis://x:6379/0", task_timeout=120)
        on_disk = json.loads((tmp_path / "config.json").read_text())
        # Only allowed keys should be present
        for key in on_disk:
            assert key in ("project_name", "redis_url", "task_timeout"), (
                f"Unexpected key persisted: {key}"
            )

    def test_save_project_roundtrips_non_secret_fields(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        ctx = SettingsService(home=tmp_path).load(project_name="p")
        updated = ProjectSettings(
            source_repo_url="https://github.com/acme/repo",
            github_owner="acme",
            github_repo="widget",
            github_base_branch="develop",
        )
        svc = SettingsService(home=tmp_path)
        svc.save_project(ctx.machine, updated)
        ctx2 = SettingsService(home=tmp_path).load(project_name="p")
        assert ctx2.project.source_repo_url == "https://github.com/acme/repo"
        assert ctx2.project.github_owner == "acme"
        assert ctx2.project.github_base_branch == "develop"

    def test_save_project_raises_without_project_name(self, tmp_path):
        machine = MachineSettings(orchestrator_home=tmp_path, project_name=None)
        settings = ProjectSettings()
        with pytest.raises(ValueError, match="project_name"):
            SettingsService(home=tmp_path).save_project(machine, settings)


# ---------------------------------------------------------------------------
# 8. for_testing factory — zero env/disk I/O
# ---------------------------------------------------------------------------


class TestForTestingFactory:
    def test_provides_full_context(self, tmp_path):
        ctx = SettingsService.for_testing(
            orchestrator_home=tmp_path,
            project_name="test",
            mode="real",
            redis_url="redis://test:6379/0",
            github_token="ghp_test",
            anthropic_api_key="sk-ant-test",
        )
        assert isinstance(ctx, SettingsContext)
        assert ctx.machine.project_name == "test"
        assert ctx.machine.mode == "real"
        assert ctx.machine.redis_url == "redis://test:6379/0"
        assert ctx.secrets.github_token == "ghp_test"
        assert ctx.secrets.anthropic_api_key == "sk-ant-test"

    def test_for_testing_is_isolated_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "env-token-should-not-leak")
        monkeypatch.setenv("PROJECT_NAME", "env-project-should-not-leak")
        ctx = SettingsService.for_testing(
            orchestrator_home=tmp_path,
            project_name="explicit-project",
            github_token="explicit-token",
        )
        assert ctx.machine.project_name == "explicit-project"
        assert ctx.secrets.github_token == "explicit-token"

    def test_for_testing_does_not_touch_disk(self, tmp_path):
        SettingsService.for_testing(orchestrator_home=tmp_path, project_name="p")
        assert not (tmp_path / "config.json").exists()
        assert not (tmp_path / "projects").exists()
