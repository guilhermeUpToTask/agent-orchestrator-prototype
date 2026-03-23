import pytest
from click.testing import CliRunner
from unittest.mock import MagicMock, patch
from src.cli import cli

@pytest.fixture
def runner():
    return CliRunner()

def test_cli_help(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Agent Orchestrator" in result.output

@patch("src.infra.factory.build_task_creation_service")
def test_create_task(mock_factory, runner):
    mock_service = MagicMock()
    mock_factory.return_value = mock_service
    mock_task = MagicMock()
    mock_task.task_id = "t1"
    mock_task.feature_id = "f1"
    mock_service.create_task.return_value = mock_task

    result = runner.invoke(cli, [
        "tasks", "create",
        "--title", "T",
        "--description", "D",
        "--capability", "c",
        "--allow", "f1.txt",
        "--allow", "f2.txt"
    ])

    assert result.exit_code == 0
    assert "t1" in result.output
    mock_service.create_task.assert_called_once()

@patch("src.infra.factory.build_task_repo")
def test_list_tasks(mock_factory, runner):
    mock_repo = MagicMock()
    mock_factory.return_value = mock_repo
    
    mock_task = MagicMock()
    mock_task.task_id = "t1"
    mock_task.status.value = "created"
    mock_task.assignment = None
    mock_task.state_version = 1
    mock_task.depends_on = []
    
    mock_repo.list_all.return_value = [mock_task]
    
    result = runner.invoke(cli, ["tasks", "list"])
    assert result.exit_code == 0
    assert "t1" in result.output
    assert "created" in result.output

@patch("src.infra.factory.build_agent_register_usecase")
def test_register_agent(mock_factory, runner):
    mock_uc = MagicMock()
    mock_factory.return_value = mock_uc
    mock_result = MagicMock()
    mock_result.agent_id = "a1"
    mock_result.active = True
    mock_result.runtime_type = "gemini"
    mock_uc.execute.return_value = mock_result
    
    result = runner.invoke(cli, [
        "agents", "create",
        "--agent-id", "a1",
        "--name", "N",
        "--capabilities", "c1,c2",
        "--runtime-config", '{"k":"v"}'
    ])
    
    assert result.exit_code == 0
    assert "a1" in result.output
    mock_uc.execute.assert_called_once()

@patch("src.infra.factory.build_reconciler")
def test_reconciler(mock_factory, runner):
    mock_reconciler = MagicMock()
    mock_factory.return_value = mock_reconciler
    
    # We don't want it to run forever in test
    mock_reconciler.run_forever.side_effect = Exception("stop")
    
    result = runner.invoke(cli, ["system", "reconciler", "--interval", "1"])
    assert result.exit_code == 1
    mock_factory.assert_called_once_with(interval_seconds=1, stuck_task_min_age_seconds=120)

@patch("src.infra.factory.build_task_manager_handler")
@patch("src.infra.factory.build_event_port")
def test_task_manager(mock_events_factory, mock_handler_factory, runner):
    mock_events = MagicMock()
    mock_events_factory.return_value = mock_events
    mock_handler = MagicMock()
    mock_handler_factory.return_value = mock_handler
    
    # Mock subscribe_many to return one event and then stop
    event = MagicMock()
    event.type = "task.created"
    event.payload = {"task_id": "t1"}
    mock_events.subscribe_many.return_value = [event]
    
    result = runner.invoke(cli, ["system", "task-manager"])
    assert result.exit_code == 0
    mock_handler.handle_task_created.assert_called_once_with("t1")

@patch("src.infra.factory.build_event_port")
@patch("src.infra.factory.build_worker_handler")
@patch("src.infra.factory.build_agent_registry")
def test_worker(mock_registry_factory, mock_handler_factory, mock_events_factory, runner):
    mock_registry = MagicMock()
    mock_registry_factory.return_value = mock_registry
    mock_handler = MagicMock()
    mock_handler_factory.return_value = mock_handler
    mock_events = MagicMock()
    mock_events_factory.return_value = mock_events
    
    event = MagicMock()
    event.payload = {"agent_id": "agent-worker-001", "task_id": "t1"}
    mock_events.subscribe.return_value = [event]
    
    result = runner.invoke(cli, ["system", "worker"])
    assert result.exit_code == 0
    mock_handler.process.assert_called_once_with(task_id="t1", project_id="")
