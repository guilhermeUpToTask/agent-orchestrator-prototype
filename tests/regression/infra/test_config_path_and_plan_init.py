"""
tests/regression/infra/test_config_path_and_plan_init.py
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.infra.settings import GlobalConfigStore, SettingsService
from src.infra.cli.plan.commands import plan_group


def _resolve_orchestrator_home() -> Path:
    import os

    raw = os.environ.get("ORCHESTRATOR_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".orchestrator"


# [ ... TestConfigPathResolution remains unchanged ... ]

# ===========================================================================
# BUG 2 — `plan init` crashing with raw SpecNotFoundError
# ===========================================================================


class TestPlanInitNoProject:
    def test_plan_init_exits_cleanly_when_no_project_configured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
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

    def test_plan_init_error_message_is_actionable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
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

        from src.infra.settings.store import GlobalConfigStore

        store = GlobalConfigStore(home=home)
        store.save({"project_name": "testing"})

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

        # Patch BOTH AppContainer references imported in plan commands
        with (
            patch("src.infra.cli.plan.commands.AppContainer") as MockAppContainer,
            patch("src.infra.cli.plan.commands._AppContainer") as MockUnderContainer,
        ):
            mock_container = MagicMock()
            MockAppContainer.from_env.return_value = mock_container
            MockUnderContainer.from_env.return_value = mock_container

            mock_orch = MagicMock()
            mock_result = MagicMock()
            mock_result.failure_reason = "dry-run: no session"
            mock_result.brief = None
            mock_orch.start_discovery.return_value = mock_result

            mock_container.planner_orchestrator = mock_orch
            mock_container.ctx.machine.project_name = "testing"

            result = runner.invoke(
                plan_group,
                ["init"],
                env={"AGENT_MODE": "dry-run"},
                catch_exceptions=False,
            )

        assert "testing" in result.output

    def test_plan_architect_exits_cleanly_when_no_project(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        runner = CliRunner()
        result = runner.invoke(
            plan_group,
            ["architect"],
            env={"AGENT_MODE": "dry-run"},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "SpecNotFoundError" not in (result.output + str(result.exception or ""))

    def test_plan_review_exits_cleanly_when_no_project(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        runner = CliRunner()
        result = runner.invoke(
            plan_group,
            ["review"],
            env={"AGENT_MODE": "dry-run"},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "SpecNotFoundError" not in (result.output + str(result.exception or ""))

    def test_plan_status_exits_cleanly_when_no_project(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
        runner = CliRunner()
        result = runner.invoke(
            plan_group,
            ["status"],
            env={"AGENT_MODE": "dry-run"},
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "SpecNotFoundError" not in (result.output + str(result.exception or ""))
