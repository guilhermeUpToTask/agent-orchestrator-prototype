from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock
from src.infra.factory import build_task_repo, build_agent_registry, build_event_port, build_agent_runtime
from src.core.models import AgentProps

class TestFactory:
    @patch("src.infra.factory.app_config")
    def test_build_task_repo(self, mock_config):
        mock_config.tasks_dir = "/tmp/tasks"
        repo = build_task_repo()
        assert repo._dir == Path("/tmp/tasks")

    @patch("src.infra.factory.app_config")
    def test_build_event_port_dry_run(self, mock_config):
        mock_config.mode = "dry-run"
        port = build_event_port()
        from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter
        assert isinstance(port, InMemoryEventAdapter)

    @patch("src.infra.factory.app_config")
    def test_build_agent_runtime_dry_run(self, mock_config):
        mock_config.mode = "dry-run"
        props = AgentProps(agent_id="a1", name="A1", runtime_type="gemini")
        runtime = build_agent_runtime(props)
        from src.infra.runtime.agent_runtime import DryRunAgentRuntime
        assert isinstance(runtime, DryRunAgentRuntime)
