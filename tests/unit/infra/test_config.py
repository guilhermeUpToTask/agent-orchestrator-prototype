"""
tests/unit/infra/test_config.py — Regression tests for SettingsService / MachineSettings.

Replaces the old OrchestratorConfig tests after config.py was deleted.
All assertions match the same behaviours — same defaults, same env-var priority,
same path derivation — now exercised through the canonical settings package.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.infra.settings import SettingsService
from src.infra.settings.models import MachineSettings, SecretSettings
from src.infra.project_paths import ProjectPaths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _svc(tmp_path: Path) -> SettingsService:
    """SettingsService pointed at tmp_path so no real ~/.orchestrator is touched."""
    return SettingsService(home=tmp_path)


# ---------------------------------------------------------------------------
# MachineSettings defaults
# ---------------------------------------------------------------------------

class TestMachineSettingsDefaults:
    def test_default_mode(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        ctx = _svc(tmp_path).load()
        assert ctx.machine.mode == "dry-run"

    def test_default_agent_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        ctx = _svc(tmp_path).load()
        assert ctx.machine.agent_id == "agent-worker-001"

    def test_default_redis_url(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        ctx = _svc(tmp_path).load()
        assert ctx.machine.redis_url == "redis://localhost:6379/0"

    def test_default_task_timeout(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        ctx = _svc(tmp_path).load()
        assert ctx.machine.task_timeout == 600

    def test_default_project_name_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        ctx = _svc(tmp_path).load()
        assert ctx.machine.project_name is None


# ---------------------------------------------------------------------------
# SecretSettings defaults
# ---------------------------------------------------------------------------

class TestSecretSettingsDefaults:
    def test_api_keys_empty_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        # Make sure no leaked env keys from outer shell
        for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "GITHUB_TOKEN"):
            monkeypatch.delenv(key, raising=False)
        ctx = _svc(tmp_path).load()
        assert ctx.secrets.anthropic_api_key == ""
        assert ctx.secrets.gemini_api_key == ""
        assert ctx.secrets.openrouter_api_key == ""
        assert ctx.secrets.github_token == ""

    def test_secrets_masked_in_repr(self):
        s = SecretSettings(anthropic_api_key="super-secret")
        assert "super-secret" not in repr(s)
        assert "super-secret" not in str(s)

    def test_secrets_shown_as_set_in_repr(self):
        s = SecretSettings(anthropic_api_key="super-secret")
        assert "***" in repr(s)


# ---------------------------------------------------------------------------
# Env-var priority
# ---------------------------------------------------------------------------

class TestEnvVarPriority:
    def test_mode_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("AGENT_MODE", "real")
        ctx = _svc(tmp_path).load()
        assert ctx.machine.mode == "real"

    def test_agent_id_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("AGENT_ID", "worker-99")
        ctx = _svc(tmp_path).load()
        assert ctx.machine.agent_id == "worker-99"

    def test_redis_url_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("REDIS_URL", "redis://remote:6380/2")
        ctx = _svc(tmp_path).load()
        assert ctx.machine.redis_url == "redis://remote:6380/2"

    def test_task_timeout_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("TASK_TIMEOUT_SECONDS", "120")
        ctx = _svc(tmp_path).load()
        assert ctx.machine.task_timeout == 120

    def test_project_name_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("PROJECT_NAME", "my-api")
        ctx = _svc(tmp_path).load()
        assert ctx.machine.project_name == "my-api"

    def test_anthropic_api_key_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        ctx = _svc(tmp_path).load()
        assert ctx.secrets.anthropic_api_key == "sk-ant-test"

    def test_gemini_api_key_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("GEMINI_API_KEY", "gm-key-test")
        ctx = _svc(tmp_path).load()
        assert ctx.secrets.gemini_api_key == "gm-key-test"

    def test_openrouter_api_key_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        ctx = _svc(tmp_path).load()
        assert ctx.secrets.openrouter_api_key == "sk-or-test"

    def test_github_token_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        ctx = _svc(tmp_path).load()
        assert ctx.secrets.github_token == "ghp_test"

    def test_env_overrides_config_json(self, tmp_path, monkeypatch):
        """Env vars beat config.json for redis_url."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("REDIS_URL", "redis://env-wins:6379/0")
        svc = _svc(tmp_path)
        svc.save_machine(redis_url="redis://from-file:6379/0")
        ctx = svc.load()
        assert ctx.machine.redis_url == "redis://env-wins:6379/0"


# ---------------------------------------------------------------------------
# Path derivation
# ---------------------------------------------------------------------------

class TestPathDerivation:
    def test_paths_scoped_under_project(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("PROJECT_NAME", "my-api")
        ctx = _svc(tmp_path).load()
        paths = ProjectPaths.for_project(ctx.machine.orchestrator_home, ctx.machine.project_name)
        project = tmp_path / "projects" / "my-api"
        assert paths.tasks_dir == project / "tasks"
        assert paths.registry_path == project / "agents" / "registry.json"
        assert paths.workspace_dir == project / "workspaces"
        assert paths.logs_dir == project / "logs"
        assert paths.events_dir == project / "events"
        assert paths.repo_url == f"file://{project / 'repo'}"

    def test_project_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("PROJECT_NAME", "my-api")
        ctx = _svc(tmp_path).load()
        assert ctx.project_home == tmp_path / "projects" / "my-api"

    def test_project_home_raises_without_project_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.delenv("PROJECT_NAME", raising=False)
        ctx = _svc(tmp_path).load()
        with pytest.raises(ValueError, match="No project configured"):
            _ = ctx.project_home


# ---------------------------------------------------------------------------
# for_testing factory
# ---------------------------------------------------------------------------

class TestForTesting:
    def test_builds_context_without_disk_io(self, tmp_path):
        ctx = SettingsService.for_testing(
            orchestrator_home=tmp_path,
            project_name="test-proj",
            mode="dry-run",
            github_token="tok",
            anthropic_api_key="key",
        )
        assert ctx.machine.project_name == "test-proj"
        assert ctx.machine.mode == "dry-run"
        assert ctx.secrets.github_token == "tok"
        assert ctx.secrets.anthropic_api_key == "key"

    def test_for_testing_no_env_reads(self, tmp_path, monkeypatch):
        """for_testing must not read env vars — all values are explicit."""
        monkeypatch.setenv("GITHUB_TOKEN", "should-not-be-read")
        ctx = SettingsService.for_testing(
            orchestrator_home=tmp_path,
            github_token="explicit-tok",
        )
        assert ctx.secrets.github_token == "explicit-tok"
