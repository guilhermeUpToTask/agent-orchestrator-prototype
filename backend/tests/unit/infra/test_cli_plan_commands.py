"""
tests/unit/infra/test_cli_plan_commands.py — plan command behaviors.

Covers the review-identified CLI bugs: the architect edit flow (edits used
to be written to a temp file, $EDITOR'd via os.system, then unlinked without
ever being read back), the review prompt matrix (answering no/no used to do
nothing silently), and --dry-run no longer mutating os.environ.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from src.infra.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


def _architecture_result(decision_content: str = "Use FastAPI."):
    decision = MagicMock()
    decision.id = "use-fastapi"
    decision.domain = "backend"
    decision.date = "2026-01-01"
    decision.content = decision_content
    decision.spec_changes = None

    result = MagicMock()
    result.failure_reason = None
    result.pending_decisions = [decision]
    result.pending_phases = []
    return result, decision


def _wire_architecture(mock_container, result):
    plan = MagicMock()
    plan.status = "architecture"
    mock_container.project_plan_repo.get.return_value = plan
    orch = mock_container.planner_orchestrator
    orch.run_architecture.return_value = result
    approval = MagicMock()
    approval.decisions_applied = 1
    approval.spec_changes_applied = 0
    approval.goals_dispatched = []
    orch.approve_architecture.return_value = approval
    return orch


class TestArchitectEditFlow:
    def test_edit_applies_changes_and_approves(self, mock_container, runner, monkeypatch):
        result, decision = _architecture_result("Original content.")
        orch = _wire_architecture(mock_container, result)

        monkeypatch.setattr("click.edit", lambda text, extension: "Edited content.")

        # Prompts: decision action = edit, approve edited = y (default),
        # approve phase plan = y
        cli_result = runner.invoke(
            cli, ["plan", "architect"], input="edit\ny\ny\n", catch_exceptions=False
        )

        assert cli_result.exit_code == 0
        assert decision.content == "Edited content."
        orch.approve_architecture.assert_called_once_with(["use-fastapi"])

    def test_aborted_edit_keeps_original_content(self, mock_container, runner, monkeypatch):
        result, decision = _architecture_result("Original content.")
        _wire_architecture(mock_container, result)

        # click.edit returns None when the editor exits without saving.
        monkeypatch.setattr("click.edit", lambda text, extension: None)

        cli_result = runner.invoke(
            cli, ["plan", "architect"], input="edit\ny\nn\n", catch_exceptions=False
        )

        assert cli_result.exit_code == 0
        assert decision.content == "Original content."


class TestReviewPromptMatrix:
    def _wire_review(self, mock_container):
        plan = MagicMock()
        plan.status = "phase_review"
        mock_container.project_plan_repo.get.return_value = plan
        orch = mock_container.planner_orchestrator
        result = MagicMock()
        result.failure_reason = None
        result.lessons = ""
        result.next_phase_proposal = None
        result.pending_decisions = []
        orch.run_phase_review.return_value = result
        approval = MagicMock()
        approval.plan_status = "phase_active"
        approval.decisions_applied = 0
        approval.goals_dispatched = []
        orch.approve_phase_review.return_value = approval
        return orch

    def test_continue_next_phase(self, mock_container, runner):
        orch = self._wire_review(mock_container)
        result = runner.invoke(cli, ["plan", "review"], input="y\n", catch_exceptions=False)
        assert result.exit_code == 0
        orch.approve_phase_review.assert_called_once_with(approve_next=True)

    def test_decline_next_but_mark_done(self, mock_container, runner):
        orch = self._wire_review(mock_container)
        result = runner.invoke(cli, ["plan", "review"], input="n\ny\n", catch_exceptions=False)
        assert result.exit_code == 0
        orch.approve_phase_review.assert_called_once_with(approve_next=False)

    def test_decline_both_takes_no_action_and_says_so(self, mock_container, runner):
        orch = self._wire_review(mock_container)
        result = runner.invoke(cli, ["plan", "review"], input="n\nn\n", catch_exceptions=False)
        assert result.exit_code == 0
        orch.approve_phase_review.assert_not_called()
        assert "No action taken" in result.output


class TestDryRunFlag:
    def test_dry_run_does_not_mutate_environ(self, mock_container, runner):
        mock_container.project_plan_repo.get.return_value = None
        before = os.environ.get("AGENT_MODE")

        runner.invoke(cli, ["plan", "architect", "--dry-run"], catch_exceptions=False)

        assert os.environ.get("AGENT_MODE") == before
