from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock
from pydantic import SecretStr
from src.infra.factory import (
    build_task_repo,
    build_agent_registry,
    build_event_port,
    build_agent_runtime,
)
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
        from src.infra.runtime.dry_run_runtime import SimulatedAgentRuntime

        assert isinstance(runtime, SimulatedAgentRuntime)

    @patch("src.infra.factory.app_config")
    def test_build_gemini_runtime_uses_config_key(self, mock_config):
        mock_config.mode = "real"
        mock_config.gemini_api_key = SecretStr("gemini-secret")
        props = AgentProps(agent_id="g1", name="G", runtime_type="gemini")
        from src.infra.runtime.gemini_runtime import GeminiAgentRuntime

        runtime = build_agent_runtime(props)
        assert isinstance(runtime, GeminiAgentRuntime)
        assert runtime._api_key == "gemini-secret"

    @patch("src.infra.factory.app_config")
    def test_build_claude_runtime_uses_config_key(self, mock_config):
        mock_config.mode = "real"
        mock_config.anthropic_api_key = SecretStr("ant-secret")
        props = AgentProps(agent_id="c1", name="C", runtime_type="claude")
        from src.infra.runtime.claude_code_runtime import ClaudeCodeRuntime

        runtime = build_agent_runtime(props)
        assert isinstance(runtime, ClaudeCodeRuntime)
        assert runtime._api_key == "ant-secret"

    @patch("src.infra.factory.app_config")
    def test_build_pi_runtime_anthropic_backend(self, mock_config):
        mock_config.mode = "real"
        mock_config.anthropic_api_key = SecretStr("ant-key")
        props = AgentProps(
            agent_id="p1",
            name="P",
            runtime_type="pi",
            runtime_config={"backend": "anthropic"},
        )
        from src.infra.runtime.pi_runtime import PiAgentRuntime

        runtime = build_agent_runtime(props)
        assert isinstance(runtime, PiAgentRuntime)
        assert runtime._api_key == "ant-key"

    @patch("src.infra.factory.app_config")
    def test_build_pi_runtime_gemini_backend(self, mock_config):
        mock_config.mode = "real"
        mock_config.gemini_api_key = SecretStr("gm-key")
        props = AgentProps(
            agent_id="p2",
            name="P2",
            runtime_type="pi",
            runtime_config={"model": "gemini-2.0-flash", "backend": "gemini"},
        )
        from src.infra.runtime.pi_runtime import PiAgentRuntime

        runtime = build_agent_runtime(props)
        assert isinstance(runtime, PiAgentRuntime)
        assert runtime._api_key == "gm-key"
        assert runtime._model == "gemini-2.0-flash"

    @patch("src.infra.factory.app_config")
    def test_build_pi_runtime_openrouter_backend(self, mock_config):
        mock_config.mode = "real"
        mock_config.openrouter_api_key = SecretStr("sk-or-key")
        props = AgentProps(
            agent_id="p3",
            name="P3",
            runtime_type="pi",
            runtime_config={"model": "anthropic/claude-sonnet-4-5", "backend": "openrouter"},
        )
        from src.infra.runtime.pi_runtime import PiAgentRuntime

        runtime = build_agent_runtime(props)
        assert isinstance(runtime, PiAgentRuntime)
        assert runtime._api_key == "sk-or-key"
        assert runtime._backend == "openrouter"
        assert runtime._env_var == "OPENROUTER_API_KEY"

    @patch("src.infra.factory.app_config")
    def test_build_pi_defaults_to_openrouter(self, mock_config):
        mock_config.mode = "real"
        mock_config.openrouter_api_key = SecretStr("sk-or-key")
        props = AgentProps(
            agent_id="p4",
            name="P4",
            runtime_type="pi",
            runtime_config={"model": "openrouter/hunter-alpha"},
        )
        from src.infra.runtime.pi_runtime import PiAgentRuntime

        runtime = build_agent_runtime(props)
        assert runtime._backend == "openrouter"
        assert runtime._api_key == "sk-or-key"

    @patch("src.infra.factory.app_config")
    def test_explicit_backend_overrides_default(self, mock_config):
        mock_config.mode = "real"
        mock_config.anthropic_api_key = SecretStr("ant-key")
        props = AgentProps(
            agent_id="p5",
            name="P5",
            runtime_type="pi",
            runtime_config={"model": "claude-sonnet-4-5", "backend": "anthropic"},
        )
        from src.infra.runtime.pi_runtime import PiAgentRuntime

        runtime = build_agent_runtime(props)
        assert runtime._backend == "anthropic"
        assert runtime._api_key == "ant-key"

    @patch("src.infra.factory.app_config")
    def test_unknown_runtime_type_raises(self, mock_config):
        mock_config.mode = "real"
        props = AgentProps(agent_id="x1", name="X", runtime_type="unknown-llm")
        with pytest.raises(ValueError, match="unknown-llm"):
            build_agent_runtime(props)
