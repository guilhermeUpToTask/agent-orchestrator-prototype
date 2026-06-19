"""End-to-end CLI tests for the `config` group (Phase 2)."""
from __future__ import annotations

import pytest
from click.testing import CliRunner
from cryptography.fernet import Fernet

from src.infra.cli.config.commands import config_group


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("AGENT_MODE", "dry-run")
    return CliRunner()


def _run(runner, *args, **kwargs):
    result = runner.invoke(config_group, list(args), catch_exceptions=False, **kwargs)
    assert result.exit_code == 0, result.output
    return result


def test_full_setup_flow(cli_env) -> None:
    runner = cli_env
    _run(runner, "create-project", "--name", "Web App", "--repo-url", "git@x:y.git")
    _run(runner, "register-provider", "--id", "anthropic", "--kind", "anthropic",
         "--api-key", "sk-ant-SECRET")
    _run(runner, "add-model", "--provider", "anthropic", "--model-id", "claude-opus-4-8")
    _run(runner, "register-agent", "--id", "w1", "--name", "Worker",
         "--runtime-type", "claude", "--provider", "anthropic",
         "--model", "claude-opus-4-8", "--capability", "code:backend")
    _run(runner, "use-project", "web-app")

    projects = _run(runner, "list-projects")
    assert "web-app" in projects.output

    # Export must never reveal the stored secret value.
    export = _run(runner, "export")
    assert "sk-ant-SECRET" not in export.output
    assert "secret://provider/anthropic" in export.output


def test_register_agent_unknown_model_fails_cleanly(cli_env) -> None:
    runner = cli_env
    _run(runner, "register-provider", "--id", "anthropic", "--kind", "anthropic",
         "--api-key", "k")
    result = runner.invoke(
        config_group,
        ["register-agent", "--id", "w1", "--name", "W", "--runtime-type", "claude",
         "--provider", "anthropic", "--model", "ghost"],
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    assert "ghost" in result.output


def test_set_secret(cli_env) -> None:
    runner = cli_env
    _run(runner, "set-secret", "--uri", "secret://provider/openai", "--value", "sk-x")
