import os
import pytest
from src.infra.config import OrchestratorConfig

def test_config_from_env_defaults(monkeypatch):
    monkeypatch.delenv("AGENT_MODE", raising=False)
    monkeypatch.delenv("AGENT_ID", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("TASK_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
    
    cfg = OrchestratorConfig.from_env()
    assert cfg.mode == "dry-run"
    assert cfg.agent_id == "agent-worker-001"
    assert cfg.redis_url == "redis://localhost:6379/0"
    assert cfg.task_timeout == 600

def test_config_from_env_custom(monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "real")
    monkeypatch.setenv("AGENT_ID", "custom-agent")
    monkeypatch.setenv("REDIS_URL", "redis://remote:6380/1")
    monkeypatch.setenv("TASK_TIMEOUT_SECONDS", "300")
    monkeypatch.setenv("ORCHESTRATOR_HOME", "/tmp/orch_test")
    
    cfg = OrchestratorConfig.from_env()
    assert cfg.mode == "real"
    assert cfg.agent_id == "custom-agent"
    assert cfg.redis_url == "redis://remote:6380/1"
    assert cfg.task_timeout == 300
    assert cfg.home_dir == "/tmp/orch_test"
    assert cfg.tasks_dir == "/tmp/orch_test/tasks"
