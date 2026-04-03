# tests/unit/infra/test_cli.py
import pytest
from click.testing import CliRunner
from unittest.mock import MagicMock

from src.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_cli_help(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Agent Orchestrator" in result.output


def test_create_task(mock_container, runner):
    mock_task = MagicMock()
    mock_task.task_id = "t1"
    mock_task.feature_id = "f1"
    mock_container.task_creation_service.create_task.return_value = mock_task

    result = runner.invoke(
        cli,
        [
            "tasks",
            "create",
            "--title",
            "T",
            "--description",
            "D",
            "--capability",
            "c",
            "--allow",
            "f1.txt",
            "--allow",
            "f2.txt",
        ],
    )

    assert result.exit_code == 0
    assert "t1" in result.output
    mock_container.task_creation_service.create_task.assert_called_once()


def test_list_tasks(mock_container, runner):
    mock_task = MagicMock()
    mock_task.task_id = "t1"
    mock_task.status.value = "created"
    mock_task.assignment = None
    mock_task.state_version = 1
    mock_task.depends_on = []

    mock_container.task_repo.list_all.return_value = [mock_task]

    result = runner.invoke(cli, ["tasks", "list"])
    assert result.exit_code == 0
    assert "t1" in result.output
    assert "created" in result.output


def test_register_agent(mock_container, runner):
    mock_result = MagicMock()
    mock_result.agent_id = "a1"
    mock_result.active = True
    mock_result.runtime_type = "gemini"
    mock_container.agent_register_usecase.execute.return_value = mock_result

    result = runner.invoke(
        cli,
        [
            "agents",
            "create",
            "--agent-id",
            "a1",
            "--name",
            "N",
            "--capabilities",
            "c1,c2",
            "--runtime-config",
            '{"k":"v"}',
        ],
    )

    assert result.exit_code == 0
    assert "a1" in result.output
    mock_container.agent_register_usecase.execute.assert_called_once()


def test_reconciler(mock_container, runner):
    mock_reconciler = mock_container.get_reconciler.return_value
    mock_reconciler.run_forever.side_effect = Exception("stop")

    result = runner.invoke(cli, ["system", "reconciler", "--interval", "1"])
    assert result.exit_code == 1
    mock_container.get_reconciler.assert_called_once_with(
        interval_seconds=1, stuck_task_min_age_seconds=120
    )


def test_task_manager(mock_container, runner):
    event = MagicMock()
    event.type = "task.created"
    event.payload = {"task_id": "t1"}
    mock_container.event_port.subscribe_many.return_value = [event]

    result = runner.invoke(cli, ["system", "task-manager"])

    assert result.exit_code == 0
    mock_container.task_manager_handler.handle_task_created.assert_called_once_with("t1")


def test_worker(mock_container, runner):
    mock_agent = MagicMock()
    mock_agent.active = True
    mock_container.agent_registry.get.return_value = mock_agent

    event = MagicMock()
    event.payload = {"agent_id": "agent-worker-001", "task_id": "t1", "project_id": ""}
    mock_container.event_port.subscribe.return_value = [event]

    result = runner.invoke(cli, ["system", "worker", "--agent-id", "agent-worker-001"])

    assert result.exit_code == 0
    mock_container.get_worker_handler.return_value.process.assert_called_once_with(
        task_id="t1", project_id=""
    )
