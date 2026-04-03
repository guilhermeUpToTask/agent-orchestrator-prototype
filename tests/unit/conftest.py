# tests/unit/conftest.py
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_container():
    """
    Mock of AppContainer matching runtime behavior:
    container.<use_case>.execute(...)
    """
    container = MagicMock()

    # Base context expectations to avoid None-type errors in commands
    container.ctx.machine.project_name = "test-project"
    container.ctx.mode = "dry-run"

    # Pre-configure chained mocks for cleaner tests
    container.task_creation_service.create_task = MagicMock()
    container.task_repo.list_all = MagicMock(return_value=[])
    container.agent_register_usecase.execute = MagicMock()
    container.get_reconciler.return_value.run_forever = MagicMock()
    container.task_manager_handler.handle_task_created = MagicMock()
    container.get_worker_handler.return_value.process = MagicMock()

    return container


@pytest.fixture(autouse=True)
def patch_app_container(mock_container):
    """
    Replace AppContainer globally for all unit tests.

    IMPORTANT: We patch the `from_env` factory method directly on the class
    so that any module importing `AppContainer` gets the mocked version
    when calling `.from_env()`.
    """
    with patch("src.infra.container.AppContainer.from_env", return_value=mock_container):
        yield mock_container
