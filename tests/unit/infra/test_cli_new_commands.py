"""
tests/unit/infra/test_cli_new_commands.py — Tests for the commands added in the
CLI roadmap: init, task retry/delete/prune, project reset.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from click.testing import CliRunner

from src.cli import cli
from src.domain import TaskStatus


@pytest.fixture()
def runner():
    return CliRunner()


# ── init --defaults ───────────────────────────────────────────────────────────


def test_init_defaults_writes_config(runner, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(cli, ["init", "--defaults"])
    assert result.exit_code == 0
    assert "Default config written" in result.output


def test_init_defaults_output_contains_key_names(runner, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(cli, ["init", "--defaults"])
    assert "project_name" in result.output
    assert "redis_url" in result.output


# ── init (interactive, mocked wizard) ────────────────────────────────────────


def test_init_interactive_delegates_to_wizard(runner):
    with patch("src.infra.cli.main.run_wizard", return_value=True) as mock_wiz:
        result = runner.invoke(cli, ["init"])
    mock_wiz.assert_called_once()
    assert result.exit_code == 0


def test_init_exits_1_when_wizard_fails(runner):
    with patch("src.infra.cli.main.run_wizard", return_value=False):
        result = runner.invoke(cli, ["init"])
    assert result.exit_code == 1


# ── task retry ────────────────────────────────────────────────────────────────


def _make_task(task_id="t-001", status=TaskStatus.FAILED):
    task = MagicMock()
    task.task_id = task_id
    task.status = status
    task.assignment = None
    return task


@patch("src.infra.factory.build_event_port")
@patch("src.infra.factory.build_task_repo")
def test_task_retry_requeues_existing_task(mock_repo_factory, mock_event_factory, runner):
    # Use case calls repo.load() (raises KeyError on miss), not repo.get().
    task = _make_task()
    repo = MagicMock()
    repo.load.return_value = task
    mock_repo_factory.return_value = repo

    events = MagicMock()
    mock_event_factory.return_value = events

    result = runner.invoke(cli, ["tasks", "retry", "t-001"])

    assert result.exit_code == 0, result.output
    assert "t-001" in result.output
    assert "requeued" in result.output
    repo.save.assert_called_once_with(task)
    events.publish.assert_called_once()


@patch("src.infra.factory.build_event_port")
@patch("src.infra.factory.build_task_repo")
def test_task_retry_exits_1_when_not_found(mock_repo_factory, mock_event_factory, runner):
    # Use case calls repo.load(); a missing task raises KeyError.
    repo = MagicMock()
    repo.load.side_effect = KeyError("no-such-task")
    mock_repo_factory.return_value = repo
    mock_event_factory.return_value = MagicMock()

    result = runner.invoke(cli, ["tasks", "retry", "no-such-task"])

    assert result.exit_code == 1
    assert "Not found" in result.output or "not found" in result.output


# ── task delete ───────────────────────────────────────────────────────────────


@patch("src.infra.factory.build_task_repo")
def test_task_delete_removes_task_with_yes_flag(mock_repo_factory, runner):
    task = _make_task()
    repo = MagicMock()
    repo.get.return_value = task
    mock_repo_factory.return_value = repo

    result = runner.invoke(cli, ["tasks", "delete", "t-001", "--yes"])

    assert result.exit_code == 0
    assert "deleted" in result.output
    repo.delete.assert_called_once_with("t-001")


@patch("src.infra.factory.build_task_repo")
def test_task_delete_exits_1_when_not_found(mock_repo_factory, runner):
    repo = MagicMock()
    repo.get.return_value = None
    mock_repo_factory.return_value = repo

    result = runner.invoke(cli, ["tasks", "delete", "no-task", "--yes"])

    assert result.exit_code == 1


@patch("src.infra.factory.build_task_repo")
def test_task_delete_prompts_without_yes(mock_repo_factory, runner):
    task = _make_task()
    repo = MagicMock()
    repo.get.return_value = task
    mock_repo_factory.return_value = repo

    # Simulate user answering 'y' to confirmation
    result = runner.invoke(cli, ["tasks", "delete", "t-001"], input="y\n")

    assert result.exit_code == 0
    repo.delete.assert_called_once()


@patch("src.infra.factory.build_task_repo")
def test_task_delete_aborts_on_no_confirmation(mock_repo_factory, runner):
    task = _make_task()
    repo = MagicMock()
    repo.get.return_value = task
    mock_repo_factory.return_value = repo

    result = runner.invoke(cli, ["tasks", "delete", "t-001"], input="n\n")

    # click.confirm(abort=True) raises Abort → exit code 1
    assert result.exit_code != 0
    repo.delete.assert_not_called()


# ── task prune ────────────────────────────────────────────────────────────────


def _make_tasks(*statuses):
    tasks = []
    for i, s in enumerate(statuses):
        t = MagicMock()
        t.task_id = f"t-{i:03d}"
        t.status = s
        tasks.append(t)
    return tasks


@patch("src.infra.factory.build_task_repo")
def test_task_prune_deletes_all_tasks(mock_repo_factory, runner):
    tasks = _make_tasks(TaskStatus.FAILED, TaskStatus.SUCCEEDED, TaskStatus.CREATED)
    repo = MagicMock()
    repo.list_all.return_value = tasks
    mock_repo_factory.return_value = repo

    result = runner.invoke(cli, ["tasks", "prune", "--yes"])

    assert result.exit_code == 0
    assert repo.delete.call_count == 3
    assert "3" in result.output


@patch("src.infra.factory.build_task_repo")
def test_task_prune_filters_by_status(mock_repo_factory, runner):
    tasks = _make_tasks(TaskStatus.FAILED, TaskStatus.SUCCEEDED, TaskStatus.FAILED)
    repo = MagicMock()
    repo.list_all.return_value = tasks
    mock_repo_factory.return_value = repo

    result = runner.invoke(cli, ["tasks", "prune", "--status", "failed", "--yes"])

    assert result.exit_code == 0
    assert repo.delete.call_count == 2


@patch("src.infra.factory.build_task_repo")
def test_task_prune_no_tasks_is_noop(mock_repo_factory, runner):
    repo = MagicMock()
    repo.list_all.return_value = []
    mock_repo_factory.return_value = repo

    result = runner.invoke(cli, ["tasks", "prune", "--yes"])

    assert result.exit_code == 0
    repo.delete.assert_not_called()
    assert "No tasks found" in result.output


@patch("src.infra.factory.build_task_repo")
def test_task_prune_invalid_status_exits_1(mock_repo_factory, runner):
    repo = MagicMock()
    repo.list_all.return_value = []
    mock_repo_factory.return_value = repo

    result = runner.invoke(cli, ["tasks", "prune", "--status", "not_a_status", "--yes"])

    assert result.exit_code == 1


# ── project reset ─────────────────────────────────────────────────────────────


@patch("src.infra.factory.build_lease_port")
@patch("src.infra.factory.build_agent_registry")
@patch("src.infra.factory.build_task_repo")
def test_project_reset_deletes_tasks_and_agents(
    mock_repo_factory, mock_reg_factory, mock_lease_factory, runner
):
    tasks = _make_tasks(TaskStatus.CREATED, TaskStatus.FAILED)
    repo = MagicMock()
    repo.list_all.return_value = tasks
    mock_repo_factory.return_value = repo

    agent = MagicMock()
    agent.agent_id = "agent-1"
    registry = MagicMock()
    registry.list_agents.return_value = [agent]
    mock_reg_factory.return_value = registry

    lease = MagicMock()
    lease.is_lease_active.return_value = False
    mock_lease_factory.return_value = lease

    result = runner.invoke(cli, ["project", "reset", "--yes"])

    assert result.exit_code == 0
    assert repo.delete.call_count == 2
    registry.deregister.assert_called_once_with("agent-1")
    assert "reset complete" in result.output


@patch("src.infra.factory.build_lease_port")
@patch("src.infra.factory.build_agent_registry")
@patch("src.infra.factory.build_task_repo")
def test_project_reset_keep_agents_skips_registry(
    mock_repo_factory, mock_reg_factory, mock_lease_factory, runner
):
    repo = MagicMock()
    repo.list_all.return_value = []
    mock_repo_factory.return_value = repo
    mock_lease_factory.return_value = MagicMock()
    mock_reg_factory.return_value = MagicMock()

    result = runner.invoke(cli, ["project", "reset", "--yes", "--keep-agents"])

    assert result.exit_code == 0
    mock_reg_factory.return_value.deregister.assert_not_called()


@patch("src.infra.factory.build_lease_port")
@patch("src.infra.factory.build_agent_registry")
@patch("src.infra.factory.build_task_repo")
def test_project_reset_prompts_without_yes(
    mock_repo_factory, mock_reg_factory, mock_lease_factory, runner
):
    repo = MagicMock()
    repo.list_all.return_value = []
    mock_repo_factory.return_value = repo
    mock_lease_factory.return_value = MagicMock()
    registry = MagicMock()
    registry.list_agents.return_value = []
    mock_reg_factory.return_value = registry

    result = runner.invoke(cli, ["project", "reset"], input="y\n")
    assert result.exit_code == 0


@patch("src.infra.factory.build_lease_port")
@patch("src.infra.factory.build_agent_registry")
@patch("src.infra.factory.build_task_repo")
def test_project_reset_aborts_on_no(
    mock_repo_factory, mock_reg_factory, mock_lease_factory, runner
):
    repo = MagicMock()
    repo.list_all.return_value = []
    mock_repo_factory.return_value = repo
    mock_lease_factory.return_value = MagicMock()
    mock_reg_factory.return_value = MagicMock()

    result = runner.invoke(cli, ["project", "reset"], input="n\n")
    assert result.exit_code != 0
    repo.delete.assert_not_called()
