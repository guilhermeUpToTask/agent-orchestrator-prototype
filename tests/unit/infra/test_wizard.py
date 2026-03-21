"""
tests/unit/infra/test_wizard.py
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from click.testing import CliRunner

from src.infra.config_manager import OrchestratorConfigManager
from src.infra.project_settings import ProjectSettingsManager
from src.infra.cli.wizard import run_wizard
from src.infra.cli.wizard.steps.deps     import check_and_report     as _check_and_report
from src.infra.cli.wizard.steps.config   import collect_project_config as _collect_project_config
from src.infra.cli.wizard.steps.registry import setup_registry         as _setup_registry
from src.infra.cli.wizard.steps.registry import _interactive_register_agent
from src.dependency_checker import DependencyReport, DepResult


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_report(*, redis=True, git=True, any_runtime=True) -> DependencyReport:
    results = [
        DepResult("redis", ok=redis, message="ok" if redis else "refused"),
        DepResult("git", ok=git, message="ok" if git else "missing"),
        DepResult(
            "gemini-cli",
            ok=any_runtime,
            message="ok" if any_runtime else "missing",
            is_runtime=True,
        ),
    ]
    return DependencyReport(results=results)


def _make_registry(agents=None) -> MagicMock:
    registry = MagicMock()
    registry.list_agents.return_value = agents or []
    return registry


# ── _check_and_report ─────────────────────────────────────────────────────────


def test_check_and_report_returns_true_when_all_ok():
    with patch("src.infra.cli.wizard.steps.deps.DependencyChecker") as MockChecker:
        MockChecker.return_value.run.return_value = _make_report()
        result = _check_and_report("redis://localhost:6379/0")
    assert result is True


def test_check_and_report_returns_false_when_redis_missing():
    with patch("src.infra.cli.wizard.steps.deps.DependencyChecker") as MockChecker:
        MockChecker.return_value.run.return_value = _make_report(redis=False)
        result = _check_and_report("redis://x:6379/0")
    assert result is False


def test_check_and_report_returns_false_when_no_runtime():
    with patch("src.infra.cli.wizard.steps.deps.DependencyChecker") as MockChecker:
        MockChecker.return_value.run.return_value = _make_report(any_runtime=False)
        result = _check_and_report("redis://localhost:6379/0")
    assert result is False


def test_check_and_report_returns_false_when_git_missing():
    with patch("src.infra.cli.wizard.steps.deps.DependencyChecker") as MockChecker:
        MockChecker.return_value.run.return_value = _make_report(git=False)
        result = _check_and_report("redis://localhost:6379/0")
    assert result is False


# ── _setup_registry ───────────────────────────────────────────────────────────


def test_setup_registry_reports_existing_agents():
    agent = MagicMock()
    agent.agent_id = "worker-1"
    agent.runtime_type = "gemini"
    agent.active = True
    registry = _make_registry(agents=[agent])

    # Should not prompt to register more — just list them
    _setup_registry({"project_name": "p", "redis_url": "r"}, lambda: registry)
    registry.register.assert_not_called()


def test_setup_registry_prompts_when_empty(monkeypatch):
    registry = _make_registry()

    # Simulate user declining to register
    monkeypatch.setattr("click.confirm", lambda *a, **kw: False)
    _setup_registry({"project_name": "p", "redis_url": "r"}, lambda: registry)
    registry.register.assert_not_called()


def test_setup_registry_registers_when_confirmed(monkeypatch):
    registry = _make_registry()

    responses = iter(["agent-x", "Worker X", "gemini", "code:backend"])
    monkeypatch.setattr("click.confirm", lambda *a, **kw: True)
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(responses))

    _setup_registry({"project_name": "p", "redis_url": "r"}, lambda: registry)
    registry.register.assert_called_once()


# ── _interactive_register_agent ───────────────────────────────────────────────


def test_interactive_register_agent(monkeypatch):
    registry = MagicMock()
    responses = iter(["my-agent", "My Agent", "claude", "code:frontend,code:backend"])
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(responses))

    _interactive_register_agent(registry)

    registry.register.assert_called_once()
    agent = registry.register.call_args[0][0]
    assert agent.agent_id == "my-agent"
    assert agent.runtime_type == "claude"
    assert "code:frontend" in agent.capabilities
    assert "code:backend" in agent.capabilities


# ── run_wizard ────────────────────────────────────────────────────────────────


def test_run_wizard_succeeds_and_writes_config(tmp_path, monkeypatch):
    # Step 1: project_name, redis_url  (orchestrator-global)
    # Step 3: source_repo_url          (project-scoped, now in project.json)
    prompts = iter(
        [
            "my-project",             # step 1 — project name
            "redis://localhost:6379",  # step 1 — redis URL
            "https://github.com/x",   # step 3 — source_repo_url (project settings)
        ]
    )
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(prompts))
    monkeypatch.setattr("click.confirm", lambda *a, **kw: False)  # skip agent reg

    with patch("src.infra.cli.wizard.steps.deps.DependencyChecker") as MockChecker:
        MockChecker.return_value.run.return_value = _make_report()
        result = run_wizard(
            cwd=tmp_path,
            registry_factory=lambda: _make_registry(),
            skip_spec=True,
        )

    assert result is True

    # Orchestrator config → .orchestrator/config.json (no source_repo_url)
    from src.infra.config import OrchestratorConfig
    orch_cfg = OrchestratorConfig(project_name="my-project")
    mgr = OrchestratorConfigManager(cwd=tmp_path)
    data = mgr.load()
    assert data["project_name"] == "my-project"
    assert "source_repo_url" not in data  # moved to project.json

    # Project settings → ~/.orchestrator/projects/my-project/project.json
    project_home = orch_cfg.orchestrator_home / "projects" / "my-project"
    ps_manager = ProjectSettingsManager(project_home)
    settings = ps_manager.load()
    assert settings.source_repo_url == "https://github.com/x"


def test_run_wizard_returns_false_when_deps_fail(tmp_path, monkeypatch):
    # Only step 1 prompts run before dep check: project_name + redis_url
    prompts = iter(["proj", "redis://x:6379"])
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(prompts))

    with patch("src.infra.cli.wizard.steps.deps.DependencyChecker") as MockChecker:
        MockChecker.return_value.run.return_value = _make_report(redis=False)
        result = run_wizard(
            cwd=tmp_path,
            registry_factory=lambda: _make_registry(),
            skip_spec=True,
        )

    assert result is False
    # Config must NOT be written if deps failed
    assert not OrchestratorConfigManager(cwd=tmp_path).exists()


def test_run_wizard_pre_fills_existing_config(tmp_path, monkeypatch):
    # Write an existing orchestrator config (no source_repo_url — that's in project.json now)
    mgr = OrchestratorConfigManager(cwd=tmp_path)
    mgr.save({"project_name": "existing", "redis_url": "redis://old:6379/0"})

    captured_defaults = {}

    def fake_prompt(text, default=None, **kw):
        captured_defaults[text] = default
        return default or ""

    monkeypatch.setattr("click.prompt", fake_prompt)
    monkeypatch.setattr("click.confirm", lambda *a, **kw: False)

    with patch("src.infra.cli.wizard.steps.deps.DependencyChecker") as MockChecker:
        MockChecker.return_value.run.return_value = _make_report()
        run_wizard(cwd=tmp_path, registry_factory=lambda: _make_registry(), skip_spec=True)

    assert "existing" in captured_defaults.values()
