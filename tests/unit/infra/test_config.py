"""
tests/unit/infra/test_config.py — Unit tests for OrchestratorConfig.
"""

from pathlib import Path
import pytest
from pydantic import SecretStr
from src.infra.config import OrchestratorConfig


class TestOrchestratorConfigDefaults:
    def test_default_mode(self):
        assert OrchestratorConfig().mode == "dry-run"

    def test_default_agent_id(self):
        assert OrchestratorConfig().agent_id == "agent-worker-001"

    def test_default_redis_url(self):
        assert OrchestratorConfig().redis_url == "redis://localhost:6379/0"

    def test_default_task_timeout(self):
        assert OrchestratorConfig().task_timeout == 600

    def test_default_project_name(self):
        assert OrchestratorConfig().project_name == "default"

    def test_default_api_keys_are_empty(self):
        cfg = OrchestratorConfig()
        assert cfg.anthropic_api_key.get_secret_value() == ""
        assert cfg.gemini_api_key.get_secret_value() == ""

    def test_api_keys_not_exposed_in_repr(self):
        cfg = OrchestratorConfig(anthropic_api_key=SecretStr("super-secret"))
        assert "super-secret" not in repr(cfg)
        assert "super-secret" not in str(cfg)


class TestOrchestratorConfigEnvVars:
    def test_mode_from_env(self, monkeypatch):
        monkeypatch.setenv("AGENT_MODE", "real")
        assert OrchestratorConfig().mode == "real"

    def test_agent_id_from_env(self, monkeypatch):
        monkeypatch.setenv("AGENT_ID", "worker-99")
        assert OrchestratorConfig().agent_id == "worker-99"

    def test_redis_url_from_env(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://remote:6380/2")
        assert OrchestratorConfig().redis_url == "redis://remote:6380/2"

    def test_task_timeout_from_env(self, monkeypatch):
        monkeypatch.setenv("TASK_TIMEOUT_SECONDS", "120")
        assert OrchestratorConfig().task_timeout == 120

    def test_project_name_from_env(self, monkeypatch):
        monkeypatch.setenv("PROJECT_NAME", "my-api")
        assert OrchestratorConfig().project_name == "my-api"

    def test_anthropic_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        assert OrchestratorConfig().anthropic_api_key.get_secret_value() == "sk-ant-test"

    def test_gemini_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gm-key-test")
        assert OrchestratorConfig().gemini_api_key.get_secret_value() == "gm-key-test"

    def test_openrouter_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        assert OrchestratorConfig().openrouter_api_key.get_secret_value() == "sk-or-test"


class TestOrchestratorConfigPaths:
    def test_paths_scoped_under_project(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("PROJECT_NAME", "my-api")
        cfg = OrchestratorConfig()
        project = tmp_path / "projects" / "my-api"
        assert cfg.tasks_dir == project / "tasks"
        assert cfg.registry_path == project / "agents" / "registry.json"
        assert cfg.workspace_dir == project / "workspaces"
        assert cfg.logs_dir == project / "logs"
        assert cfg.events_dir == project / "events"
        assert cfg.repo_url == f"file://{project / 'repo'}"

    def test_project_home_property(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("PROJECT_NAME", "my-api")
        cfg = OrchestratorConfig()
        assert cfg.project_home == tmp_path / "projects" / "my-api"

    def test_default_project_paths(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        cfg = OrchestratorConfig()
        project = tmp_path / "projects" / "default"
        assert cfg.tasks_dir == project / "tasks"
        assert cfg.logs_dir == project / "logs"
        assert cfg.events_dir == project / "events"

    def test_explicit_tasks_dir_overrides_derived(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("TASKS_DIR", "/custom/tasks")
        cfg = OrchestratorConfig()
        assert cfg.tasks_dir == Path("/custom/tasks")

    def test_home_dir_compat_property(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        cfg = OrchestratorConfig()
        assert cfg.home_dir == str(tmp_path)
        assert isinstance(cfg.home_dir, str)


class TestOrchestratorConfigDirectInit:
    """Verify configs can be built directly (useful in tests and fixtures)."""

    def test_direct_mode_override(self):
        cfg = OrchestratorConfig(mode="real")
        assert cfg.mode == "real"

    def test_direct_api_key_override(self):
        cfg = OrchestratorConfig(anthropic_api_key=SecretStr("direct-key"))
        assert cfg.anthropic_api_key.get_secret_value() == "direct-key"

    def test_from_env_classmethod(self):
        cfg = OrchestratorConfig.from_env()
        assert isinstance(cfg, OrchestratorConfig)
