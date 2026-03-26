"""
tests/regression/infra/test_config_path_and_plan_init.py

Regression tests for two critical bugs:

BUG 1 — config.json written to wrong path
  OrchestratorConfigManager was resolving the config file relative to
  Path.cwd(), so it wrote to <cwd>/.orchestrator/config.json.
  OrchestratorConfig (via LocalJsonConfigSource) read from the same CWD-
  relative path — but only when the CWD happened to be the project root.
  In practice the file was never found because the canonical home is
  ~/.orchestrator (or whatever ORCHESTRATOR_HOME is set to).

  Fix: both OrchestratorConfigManager and LocalJsonConfigSource resolve the
  path from the ORCHESTRATOR_HOME env var (with expanduser()) and fall back
  to ~/.orchestrator.  They must always agree on the same file.

BUG 2 — `plan init` crashes with raw SpecNotFoundError when no project configured
  When project_name was "default" (or unset) and no project_spec.yaml existed
  for that name, `build_planner_orchestrator()` called load_project_spec()
  immediately and raised SpecNotFoundError as an unhandled exception, printing
  a full traceback instead of a clear user-facing message.

  Fix: plan commands call _require_project() first (aborts with a clear message
  if project_name is None) and wrap build_planner_orchestrator() to catch
  SpecNotFoundError and surface it as a clean CLI error.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.infra.config_manager import OrchestratorConfigManager, _resolve_orchestrator_home
from src.infra.config import OrchestratorConfig, LocalJsonConfigSource
from src.infra.cli.plan.commands import plan_group


# ===========================================================================
# BUG 1 — config.json path resolution
# ===========================================================================


class TestConfigPathResolution:
    """
    OrchestratorConfigManager and LocalJsonConfigSource must always resolve
    the config file to the same path, and that path must be derived from
    ORCHESTRATOR_HOME (not CWD).
    """

    def test_manager_uses_orchestrator_home_env_not_cwd(
        self, tmp_path, monkeypatch
    ):
        """
        REGRESSION: manager used Path.cwd() so the file ended up in a random
        directory that LocalJsonConfigSource never read.
        """
        home = tmp_path / "orch_home"
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))

        # CWD is somewhere completely different
        monkeypatch.chdir(tmp_path / "some_other_dir" if False else tmp_path)

        mgr = OrchestratorConfigManager()
        mgr.save({"project_name": "testing", "redis_url": "redis://localhost:6379/0"})

        expected = home / "config.json"
        assert expected.exists(), (
            f"config.json was not written to ORCHESTRATOR_HOME ({home}). "
            f"It may have been written to CWD instead."
        )
        assert mgr.config_path == expected

    def test_manager_expands_tilde_in_orchestrator_home(
        self, tmp_path, monkeypatch
    ):
        """
        REGRESSION: ORCHESTRATOR_HOME=~/.orchestrator contains a literal ~.
        Without expanduser() the path is treated as a relative directory
        named '~' instead of the user's home.
        """
        # Point home to a real tmp directory but set the env var with a tilde
        # We can't actually use ~ in tests without writing to the real home dir,
        # so we verify that _resolve_orchestrator_home() calls expanduser()
        # by checking a value that contains ~ is expanded correctly.
        real_home = Path.home()
        monkeypatch.setenv("ORCHESTRATOR_HOME", "~/.orchestrator")

        resolved = _resolve_orchestrator_home()

        assert not str(resolved).startswith("~"), (
            "ORCHESTRATOR_HOME tilde was not expanded. "
            f"Got: {resolved}"
        )
        assert resolved == real_home / ".orchestrator"

    def test_local_json_config_source_reads_from_orchestrator_home(
        self, tmp_path, monkeypatch
    ):
        """
        REGRESSION: LocalJsonConfigSource read from Path.cwd()/.orchestrator/
        instead of ORCHESTRATOR_HOME, so config written by the wizard was
        never picked up by OrchestratorConfig.
        """
        home = tmp_path / "orch_home"
        home.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))

        # Write config directly to the expected location
        config_path = home / "config.json"
        config_path.write_text(
            json.dumps({"project_name": "testing", "redis_url": "redis://r:6379/0"}),
            encoding="utf-8",
        )

        # OrchestratorConfig must pick up the project_name from that file
        cfg = OrchestratorConfig()

        assert cfg.project_name == "testing", (
            "OrchestratorConfig did not read project_name from "
            f"ORCHESTRATOR_HOME/config.json ({config_path}). "
            "LocalJsonConfigSource is likely still using CWD."
        )

    def test_manager_and_config_agree_on_same_path(
        self, tmp_path, monkeypatch
    ):
        """
        The writer (OrchestratorConfigManager) and reader (LocalJsonConfigSource
        inside OrchestratorConfig) must resolve to the exact same file path.
        If they disagree, wizard output is silently lost.
        """
        home = tmp_path / "orch_home"
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))

        mgr = OrchestratorConfigManager()
        mgr.save({"project_name": "my-project", "redis_url": "redis://localhost:6379/0"})

        cfg = OrchestratorConfig()

        assert cfg.project_name == "my-project", (
            "OrchestratorConfig could not read back the project_name that "
            "OrchestratorConfigManager just wrote.  The two components are "
            "resolving config.json to different paths."
        )

    def test_config_written_before_dep_check_is_persisted(
        self, tmp_path, monkeypatch
    ):
        """
        REGRESSION: the wizard wrote config.json AFTER the dep check, so if
        deps failed the file was never created.  Now it must be written
        immediately after Step 1, regardless of dep check outcome.
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

        with patch(
            "src.infra.cli.wizard.steps.deps.DependencyChecker"
        ) as MockChecker:
            MockChecker.return_value.run.return_value = failing_report
            result = run_wizard(home=home, skip_spec=True)

        # Wizard should fail due to redis
        assert result is False

        # BUT config.json must already exist — written before the dep check
        mgr = OrchestratorConfigManager()
        assert mgr.exists(), (
            "config.json was not written even though the user completed Step 1. "
            "The wizard must persist config before running the dep check."
        )
        assert mgr.load()["project_name"] == "testing"

    def test_no_default_project_name_without_config(
        self, tmp_path, monkeypatch
    ):
        """
        REGRESSION: project_name defaulted to 'default' so commands silently
        operated on a non-existent project.  Without a config.json the value
        must be None, forcing an explicit error.
        """
        home = tmp_path / "fresh_home"
        home.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))

        cfg = OrchestratorConfig()

        assert cfg.project_name is None, (
            f"Expected project_name to be None when no config exists, "
            f"got '{cfg.project_name}'. The old default 'default' caused "
            f"commands to silently target a non-existent project."
        )


# ===========================================================================
# BUG 2 — `plan init` crashing with raw SpecNotFoundError
# ===========================================================================


class TestPlanInitNoProject:
    """
    `plan init` must never show a raw Python traceback. When no project is
    configured it must exit with a clear, actionable message.
    """

    def _run_plan_init(self, project_name=None, extra_env=None):
        """Invoke `plan init` via Click's test runner with a controlled config."""
        runner = CliRunner()
        env = {"AGENT_MODE": "dry-run"}
        if project_name:
            env["PROJECT_NAME"] = project_name
        if extra_env:
            env.update(extra_env)
        return runner.invoke(plan_group, ["init"], env=env, catch_exceptions=False)

    def test_plan_init_exits_cleanly_when_no_project_configured(self):
        """
        REGRESSION: without a project_name, plan init raised SpecNotFoundError
        with a full Python traceback.  It must now exit with code 1 and a
        human-readable message — no traceback.
        """
        runner = CliRunner()
        # No PROJECT_NAME in env → project_name is None
        result = runner.invoke(
            plan_group,
            ["init"],
            env={"AGENT_MODE": "dry-run"},
            catch_exceptions=False,
        )

        assert result.exit_code != 0, (
            "plan init should have exited non-zero when no project is configured"
        )
        assert "SpecNotFoundError" not in (result.output + str(result.exception or "")), (
            "plan init exposed a raw SpecNotFoundError traceback to the user. "
            "It must be caught and shown as a clean error message."
        )
        assert "Traceback" not in result.output, (
            "plan init printed a Python traceback instead of a user-friendly message."
        )

    def test_plan_init_error_message_is_actionable(self):
        """
        The error shown to the user must tell them what to do next.
        """
        runner = CliRunner()
        result = runner.invoke(
            plan_group,
            ["init"],
            env={"AGENT_MODE": "dry-run"},
            catch_exceptions=False,
        )

        output = result.output.lower() + str(result.exception or "").lower()
        assert "init" in output, (
            "Error message should mention 'init' so the user knows what to run. "
            f"Got: {result.output!r}"
        )

    def test_plan_init_shows_project_name_when_configured(
        self, tmp_path, monkeypatch
    ):
        """
        When a valid project IS configured, plan init must display the active
        project name before doing any work — so the user can confirm they are
        operating on the right project.
        """
        home = tmp_path / "orch_home"
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
        monkeypatch.setenv("PROJECT_NAME", "testing")

        # Create the minimum files needed so plan init gets past _require_project
        # and build_planner_orchestrator — give it a real spec file
        project_dir = home / "projects" / "testing"
        project_dir.mkdir(parents=True)
        spec_data = {
            "name": "testing",
            "meta": {"version": "0.1.0", "created_at": "2024-01-01T00:00:00"},
            "objective": {
                "description": "Test project",
                "domain": "developer-tooling",
            },
            "tech_stack": {"backend": ["python"], "database": ["redis"], "infra": []},
            "constraints": {"required": [], "forbidden": []},
            "directories": [],
        }
        import yaml
        (project_dir / "project_spec.yaml").write_text(
            yaml.dump(spec_data), encoding="utf-8"
        )

        runner = CliRunner()
        # Patch the heavy orchestrator build so we only test the guard layer
        with patch(
            "src.infra.cli.plan.commands.build_planner_orchestrator"
        ) as mock_build:
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

        assert "testing" in result.output, (
            "plan init must display the active project name so the user "
            f"knows which project they are operating on. Output: {result.output!r}"
        )

    def test_plan_architect_exits_cleanly_when_no_project(self):
        """plan architect has the same guard — verify it too."""
        runner = CliRunner()
        result = runner.invoke(
            plan_group,
            ["architect"],
            env={"AGENT_MODE": "dry-run"},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "SpecNotFoundError" not in (result.output + str(result.exception or ""))
        assert "Traceback" not in result.output

    def test_plan_review_exits_cleanly_when_no_project(self):
        """plan review has the same guard — verify it too."""
        runner = CliRunner()
        result = runner.invoke(
            plan_group,
            ["review"],
            env={"AGENT_MODE": "dry-run"},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "SpecNotFoundError" not in (result.output + str(result.exception or ""))
        assert "Traceback" not in result.output

    def test_plan_status_exits_cleanly_when_no_project(self):
        """plan status has the same guard — verify it too."""
        runner = CliRunner()
        result = runner.invoke(
            plan_group,
            ["status"],
            env={"AGENT_MODE": "dry-run"},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "SpecNotFoundError" not in (result.output + str(result.exception or ""))
        assert "Traceback" not in result.output
