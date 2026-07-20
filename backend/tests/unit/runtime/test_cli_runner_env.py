from __future__ import annotations

import pytest

from src.infra.runtime.cli_runner import ClaudeCodeRunner, GeminiRunner, PiAgentRunner


@pytest.mark.parametrize(
    ("runner", "api_key_name", "api_key"),
    [
        (
            lambda: PiAgentRunner(api_key="pi-key", model="model", backend="anthropic"),
            "ANTHROPIC_API_KEY",
            "pi-key",
        ),
        (lambda: ClaudeCodeRunner(api_key="claude-key"), "ANTHROPIC_API_KEY", "claude-key"),
        (lambda: GeminiRunner(api_key="gemini-key", model="model"), "GEMINI_API_KEY", "gemini-key"),
    ],
)
def test_runner_env_scrubs_worker_only_variables(monkeypatch, runner, api_key_name, api_key):
    monkeypatch.setenv("ORCHESTRATOR_HOME", "/worker/orchestrator")
    monkeypatch.setenv("PROJECT_REPO_DIR", "/worker/repo")
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", "master-key")
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "api-token")
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.setenv("HOME", "/home/agent")

    child_env = runner()._env()

    assert child_env["PATH"] == "/bin"
    assert child_env["HOME"] == "/home/agent"
    assert child_env[api_key_name] == api_key
    assert "ORCHESTRATOR_HOME" not in child_env
    assert "PROJECT_REPO_DIR" not in child_env
    assert "ORCHESTRATOR_MASTER_KEY" not in child_env
    assert "ORCHESTRATOR_API_TOKEN" not in child_env


@pytest.mark.parametrize(
    "runner",
    [
        lambda: PiAgentRunner(api_key="pi-key", model="model"),
        lambda: ClaudeCodeRunner(api_key="claude-key"),
        lambda: GeminiRunner(api_key="gemini-key", model="model"),
    ],
)
def test_runner_env_omits_absent_allowlisted_variables(monkeypatch, runner):
    names = (
        "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TERM",
        "TMPDIR", "TZ", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)

    child_env = runner()._env()

    assert all(name not in child_env for name in names)
