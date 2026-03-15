"""
tests/unit/infra/test_config.py — Unit tests for OrchestratorConfig.
"""
from pathlib import Path
import pytest
from pydantic import SecretStr
from src.infra.config import OrchestratorConfig


class TestOrchestratorConfigDefaults:
    def test_default_mode(self):
        cfg = OrchestratorConfig()
        assert cfg.mode == "dry-run"

    def test_default_agent_id(self):
        cfg = OrchestratorConfig()
        assert cfg.agent_id == "agent-worker-001"

    def test_default_redis_url(self):
        cfg = OrchestratorConfig()
        assert cfg.redis_url == "redis://localhost:6379/0"

    def test_default_task_timeout(self):
        cfg = OrchestratorConfig()
        assert cfg.task_timeout == 600

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
        cfg = OrchestratorConfig()
        assert cfg.mode == "real"

    def test_agent_id_from_env(self, monkeypatch):
        monkeypatch.setenv("AGENT_ID", "worker-99")
        cfg = OrchestratorConfig()
        assert cfg.agent_id == "worker-99"

    def test_redis_url_from_env(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://remote:6380/2")
        cfg = OrchestratorConfig()
        assert cfg.redis_url == "redis://remote:6380/2"

    def test_task_timeout_from_env(self, monkeypatch):
        monkeypatch.setenv("TASK_TIMEOUT_SECONDS", "120")
        cfg = OrchestratorConfig()
        assert cfg.task_timeout == 120

    def test_anthropic_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        cfg = OrchestratorConfig()
        assert cfg.anthropic_api_key.get_secret_value() == "sk-ant-test"

    def test_gemini_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gm-key-test")
        cfg = OrchestratorConfig()
        assert cfg.gemini_api_key.get_secret_value() == "gm-key-test"

    def test_openrouter_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        cfg = OrchestratorConfig()
        assert cfg.openrouter_api_key.get_secret_value() == "sk-or-test"


class TestOrchestratorConfigPaths:
    def test_derived_tasks_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        cfg = OrchestratorConfig()
        assert cfg.tasks_dir == tmp_path / "tasks"

    def test_derived_registry_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        cfg = OrchestratorConfig()
        assert cfg.registry_path == tmp_path / "agents" / "registry.json"

    def test_derived_workspace_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        cfg = OrchestratorConfig()
        assert cfg.workspace_dir == tmp_path / "repos" / "workspaces"

    def test_derived_repo_url(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        cfg = OrchestratorConfig()
        assert cfg.repo_url == f"file://{tmp_path / 'repos' / 'my-repo'}"

    def test_explicit_tasks_dir_overrides_derived(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("TASKS_DIR", "/custom/tasks")
        cfg = OrchestratorConfig()
        assert cfg.tasks_dir == Path("/custom/tasks")

    def test_explicit_registry_path_overrides_derived(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        monkeypatch.setenv("REGISTRY_PATH", "/custom/registry.json")
        cfg = OrchestratorConfig()
        assert cfg.registry_path == Path("/custom/registry.json")

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