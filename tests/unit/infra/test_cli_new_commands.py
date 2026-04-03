# tests/unit/infra/test_cli_new_commands.py
import pytest
from click.testing import CliRunner
from unittest.mock import MagicMock, patch

from src.cli import cli
from src.domain import TaskStatus


# TODO: merge this file with the teest_cli


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
    # project_name is None by default and is excluded from output.
    # Check for actual defaults printed:
    assert "redis_url" in result.output
    assert "task_timeout" in result.output


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


def test_task_retry_requeues_existing_task(mock_container, runner):
    mock_result = MagicMock()
    mock_result.previous_status = TaskStatus.FAILED
    mock_container.task_retry_usecase.execute.return_value = mock_result

    result = runner.invoke(cli, ["tasks", "retry", "t-001"])

    assert result.exit_code == 0, result.output
    assert "t-001" in result.output
    assert "requeued" in result.output
    mock_container.task_retry_usecase.execute.assert_called_once_with("t-001")


def test_task_retry_exits_1_when_not_found(mock_container, runner):
    mock_container.task_retry_usecase.execute.side_effect = KeyError("no-such-task")

    result = runner.invoke(cli, ["tasks", "retry", "no-such-task"])

    assert result.exit_code == 1
    assert "Not found" in result.output or "not found" in result.output


# ── task delete ───────────────────────────────────────────────────────────────


def test_task_delete_removes_task_with_yes_flag(mock_container, runner):
    mock_container.task_repo.get.return_value = _make_task()

    result = runner.invoke(cli, ["tasks", "delete", "t-001", "--yes"])

    assert result.exit_code == 0
    assert "deleted" in result.output
    mock_container.task_delete_usecase.execute.assert_called_once_with("t-001")


def test_task_delete_exits_1_when_not_found(mock_container, runner):
    mock_container.task_repo.get.return_value = None

    result = runner.invoke(cli, ["tasks", "delete", "no-task", "--yes"])

    assert result.exit_code == 1


def test_task_delete_prompts_without_yes(mock_container, runner):
    mock_container.task_repo.get.return_value = _make_task()

    result = runner.invoke(cli, ["tasks", "delete", "t-001"], input="y\n")

    assert result.exit_code == 0
    mock_container.task_delete_usecase.execute.assert_called_once()


def test_task_delete_aborts_on_no_confirmation(mock_container, runner):
    mock_container.task_repo.get.return_value = _make_task()

    result = runner.invoke(cli, ["tasks", "delete", "t-001"], input="n\n")

    assert result.exit_code != 0
    mock_container.task_delete_usecase.execute.assert_not_called()


# ── task prune ────────────────────────────────────────────────────────────────


def _make_tasks(*statuses):
    return [_make_task(f"t-{i:03d}", s) for i, s in enumerate(statuses)]


def test_task_prune_deletes_all_tasks(mock_container, runner):
    mock_container.task_repo.list_all.return_value = _make_tasks(
        TaskStatus.FAILED, TaskStatus.SUCCEEDED, TaskStatus.CREATED
    )

    mock_result = MagicMock()
    mock_result.count = 3
    mock_container.task_prune_usecase.execute.return_value = mock_result

    result = runner.invoke(cli, ["tasks", "prune", "--yes"])

    assert result.exit_code == 0
    assert "3" in result.output
    mock_container.task_prune_usecase.execute.assert_called_once()


def test_task_prune_filters_by_status(mock_container, runner):
    mock_container.task_repo.list_all.return_value = _make_tasks(
        TaskStatus.FAILED, TaskStatus.SUCCEEDED, TaskStatus.FAILED
    )

    mock_result = MagicMock()
    mock_result.count = 2
    mock_container.task_prune_usecase.execute.return_value = mock_result

    result = runner.invoke(cli, ["tasks", "prune", "--status", "failed", "--yes"])

    assert result.exit_code == 0
    mock_container.task_prune_usecase.execute.assert_called_once()


def test_task_prune_no_tasks_is_noop(mock_container, runner):
    mock_container.task_repo.list_all.return_value = []

    mock_result = MagicMock()
    mock_result.count = 0
    mock_container.task_prune_usecase.execute.return_value = mock_result

    result = runner.invoke(cli, ["tasks", "prune", "--yes"])

    assert result.exit_code == 0
    assert "No tasks found" in result.output


def test_task_prune_invalid_status_exits_1(mock_container, runner):
    result = runner.invoke(cli, ["tasks", "prune", "--status", "not_a_status", "--yes"])
    assert result.exit_code == 1


# ── project reset ─────────────────────────────────────────────────────────────


def test_project_reset_deletes_tasks_and_agents(mock_container, runner):
    mock_result = MagicMock()
    mock_result.tasks_deleted = 2
    mock_result.leases_released = 1
    mock_result.branches_deleted = 0
    mock_result.agents_removed = 1
    mock_result.had_errors = False
    mock_container.project_reset_usecase.execute.return_value = mock_result

    result = runner.invoke(cli, ["project", "reset", "--yes"])

    assert result.exit_code == 0
    mock_container.project_reset_usecase.execute.assert_called_once_with(keep_agents=False)
    assert "reset complete" in result.output


def test_project_reset_keep_agents_skips_registry(mock_container, runner):
    mock_result = MagicMock()
    mock_result.had_errors = False
    mock_container.project_reset_usecase.execute.return_value = mock_result

    result = runner.invoke(cli, ["project", "reset", "--yes", "--keep-agents"])

    assert result.exit_code == 0
    mock_container.project_reset_usecase.execute.assert_called_once_with(keep_agents=True)


def test_project_reset_prompts_without_yes(mock_container, runner):
    mock_result = MagicMock()
    mock_result.had_errors = False
    mock_container.project_reset_usecase.execute.return_value = mock_result

    result = runner.invoke(cli, ["project", "reset"], input="y\n")
    assert result.exit_code == 0


def test_project_reset_aborts_on_no(mock_container, runner):
    result = runner.invoke(cli, ["project", "reset"], input="n\n")
    assert result.exit_code != 0
    mock_container.project_reset_usecase.execute.assert_not_called()
