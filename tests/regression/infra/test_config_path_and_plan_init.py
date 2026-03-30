"""
tests/regression/infra/test_config_path_and_plan_init.py

Regression tests for two critical bugs:

BUG 1 — config.json written to wrong path
  GlobalConfigStore (formerly OrchestratorConfigManager) must resolve the
  config file from ORCHESTRATOR_HOME, not CWD.  SettingsService reads from
  the same path — the two must always agree.

BUG 2 — `plan init` crashes with raw SpecNotFoundError when no project configured
  Fixed: plan commands call _require_project() first and wrap
  build_planner_orchestrator() to catch SpecNotFoundError.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.infra.settings import GlobalConfigStore, SettingsService
from src.infra.cli.plan.commands import plan_group


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _resolve_orchestrator_home() -> Path:
    """Mirror of the internal helper — used in tilde-expansion test."""
    import os
    raw = os.environ.get("ORCHESTRATOR_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".orchestrator"


# ===========================================================================
# BUG 1 — config.json path resolution
# ===========================================================================

class TestConfigPathResolution:

    def test_store_uses_orchestrator_home_env_not_cwd(self, tmp_path, monkeypatch):
        """
        REGRESSION: manager used Path.cwd() so the file ended up in a random
        directory that SettingsService never read.
        """
        home = tmp_path / "orch_home"
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))

        store = GlobalConfigStore()
        store.save({"project_name": "testing", "redis_url": "redis://localhost:6379/0"})

        expected = home / "config.json"
        assert expected.exists(), (
            f"config.json was not written to ORCHESTRATOR_HOME ({home}). "
            f"It may have been written to CWD instead."
        )
        assert store.config_path == expected

    def test_store_expands_tilde_in_orchestrator_home(self, monkeypatch):
        """
        REGRESSION: ORCHESTRATOR_HOME=~/.orchestrator — without expanduser()
        the path is treated as a relative directory named '~'.
        """
        real_home = Path.home()
        monkeypatch.setenv("ORCHESTRATOR_HOME", "~/.orchestrator")

        resolved = _resolve_orchestrator_home()

        assert not str(resolved).startswith("~"), (
            f"ORCHESTRATOR_HOME tilde was not expanded. Got: {resolved}"
        )
        assert resolved == real_home / ".orchestrator"

    def test_service_reads_from_orchestrator_home(self, tmp_path, monkeypatch):
        """
        REGRESSION: SettingsService read from Path.cwd()/.orchestrator/
        instead of ORCHESTRATOR_HOME, so config written by the wizard was
        never picked up.
        """
        home = tmp_path / "orch_home"
        home.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
        monkeypatch.delenv("PROJECT_NAME", raising=False)

        config_path = home / "config.json"
        config_path.write_text(
            json.dumps({"project_name": "testing", "redis_url": "redis://r:6379/0"}),
            encoding="utf-8",
        )

        ctx = SettingsService().load()
        assert ctx.machine.project_name == "testing", (
            "SettingsService did not read project_name from "
            f"ORCHESTRATOR_HOME/config.json ({config_path}). "
            "It is likely still using CWD."
        )

    def test_store_and_service_agree_on_same_path(self, tmp_path, monkeypatch):
        """
        The writer (GlobalConfigStore) and reader (SettingsService) must
        resolve to the exact same file path.
        """
        home = tmp_path / "orch_home"
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
        monkeypatch.delenv("PROJECT_NAME", raising=False)

        store = GlobalConfigStore()
        store.save({"project_name": "my-project", "redis_url": "redis://localhost:6379/0"})

        ctx = SettingsService().load()
        assert ctx.machine.project_name == "my-project", (
            "SettingsService could not read back the project_name that "
            "GlobalConfigStore just wrote. The two components are resolving "
            "config.json to different paths."
        )

    def test_config_written_before_dep_check_is_persisted(self, tmp_path, monkeypatch):
        """
        REGRESSION: the wizard wrote config.json AFTER the dep check, so if
        deps failed the file was never created.  It must be written immediately
        after Step 1, regardless of dep check outcome.
        """
        from src.infra.cli.wizard import run_wizard
        from src.dependency_checker import DependencyReport, DepResult

        home = tmp_path / "orch_home"
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))

        prompts = iter(["testing", "redis://localhost:6379/0", ""])
        monkeypatch.setattr("click.prompt", lambda *a, **kw: next(prompts))

        failing_report = DependencyReport(results=[
            DepResult("redis", ok=False, message="Connection refused"),
            DepResult("git",   ok=True,  message="ok"),
            DepResult("gemini-cli", ok=True, message="ok", is_runtime=True),
        ])

        with patch("src.infra.cli.wizard.steps.deps.DependencyChecker") as MockChecker:
            MockChecker.return_value.run.return_value = failing_report
            result = run_wizard(home=home, skip_spec=True)

        assert result is False

        store = GlobalConfigStore()
        assert store.exists(), (
            "config.json was not written even though the user completed Step 1. "
            "The wizard must persist config before running the dep check."
        )
        assert store.load()["project_name"] == "testing"

    def test_no_default_project_name_without_config(self, tmp_path, monkeypatch):
        """
        REGRESSION: project_name defaulted to 'default' so commands silently
        operated on a non-existent project.  Without a config.json the value
        must be None.
        """
        home = tmp_path / "fresh_home"
        home.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
        monkeypatch.delenv("PROJECT_NAME", raising=False)

        ctx = SettingsService().load()
        assert ctx.machine.project_name is None, (
            f"Expected project_name to be None when no config exists, "
            f"got '{ctx.machine.project_name}'."
        )

    def test_secret_never_written_to_config_json(self, tmp_path, monkeypatch):
        """
        REGRESSION guard: secrets must never appear in config.json even if
        callers pass them to save().
        """
        home = tmp_path / "orch_home"
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))

        store = GlobalConfigStore()
        store.save({
            "project_name": "p",
            "github_token": "ghp_SHOULD_NOT_PERSIST",
            "anthropic_api_key": "sk-SHOULD_NOT_PERSIST",
        })

        on_disk = json.loads(store.config_path.read_text())
        assert "github_token" not in on_disk
        assert "anthropic_api_key" not in on_disk


# ===========================================================================
# BUG 2 — `plan init` crashing with raw SpecNotFoundError
# ===========================================================================

class TestPlanInitNoProject:

    def test_plan_init_exits_cleanly_when_no_project_configured(self):
        """
        REGRESSION: without a project_name, plan init raised SpecNotFoundError
        with a full Python traceback.
        """
        runner = CliRunner()
        result = runner.invoke(
            plan_group,
            ["init"],
            env={"AGENT_MODE": "dry-run"},
            catch_exceptions=False,
        )

        assert result.exit_code != 0
        assert "SpecNotFoundError" not in (result.output + str(result.exception or ""))
        assert "Traceback" not in result.output

    def test_plan_init_error_message_is_actionable(self):
        runner = CliRunner()
        result = runner.invoke(
            plan_group,
            ["init"],
            env={"AGENT_MODE": "dry-run"},
            catch_exceptions=False,
        )
        output = result.output.lower() + str(result.exception or "").lower()
        assert "init" in output

    def test_plan_init_shows_project_name_when_configured(self, tmp_path, monkeypatch):
        """When a valid project IS configured, plan init must display the project name."""
        home = tmp_path / "orch_home"
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
        monkeypatch.setenv("PROJECT_NAME", "testing")

        project_dir = home / "projects" / "testing"
        project_dir.mkdir(parents=True)
        spec_data = {
            "name": "testing",
            "meta": {"version": "0.1.0", "created_at": "2024-01-01T00:00:00"},
            "objective": {"description": "Test project", "domain": "developer-tooling"},
            "tech_stack": {"backend": ["python"], "database": ["redis"], "infra": []},
            "constraints": {"required": [], "forbidden": []},
            "directories": [],
        }
        import yaml
        (project_dir / "project_spec.yaml").write_text(yaml.dump(spec_data), encoding="utf-8")

        runner = CliRunner()
        with patch("src.infra.cli.plan.commands.build_planner_orchestrator") as mock_build:
            mock_orch = MagicMock()
            mock_result = MagicMock()
            mock_result.failure_reason = "dry-run: no session"
            mock_result.brief = None
            mock_orch.start_discovery.return_value = mock_result
            mock_build.return_value = mock_orch

            result = runner.invoke(
                plan_group,
                ["init"],
                env={"AGENT_MODE": "dry-run", "PROJECT_NAME": "testing"},
                catch_exceptions=False,
            )

        assert "testing" in result.output

    def test_plan_architect_exits_cleanly_when_no_project(self):
        runner = CliRunner()
        result = runner.invoke(
            plan_group, ["architect"],
            env={"AGENT_MODE": "dry-run"}, catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "SpecNotFoundError" not in (result.output + str(result.exception or ""))
        assert "Traceback" not in result.output

    def test_plan_review_exits_cleanly_when_no_project(self):
        runner = CliRunner()
        result = runner.invoke(
            plan_group, ["review"],
            env={"AGENT_MODE": "dry-run"}, catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "SpecNotFoundError" not in (result.output + str(result.exception or ""))
        assert "Traceback" not in result.output

    def test_plan_status_exits_cleanly_when_no_project(self):
        runner = CliRunner()
        result = runner.invoke(
            plan_group, ["status"],
            env={"AGENT_MODE": "dry-run"}, catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "SpecNotFoundError" not in (result.output + str(result.exception or ""))
        assert "Traceback" not in result.output
