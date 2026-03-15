import pytest
from unittest.mock import MagicMock, patch
from src.core.models import AgentProps, ExecutionContext, ExecutionSpec
from src.infra.runtime.gemini_runtime import GeminiAgentRuntime

class TestGeminiAgentRuntime:
    @patch("subprocess.run")
    def test_run_task_calls_gemini_cli(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"Gemini output\nModified: a.py",
            stderr=b""
        )
        
        runtime = GeminiAgentRuntime(api_key="fake-key")
        ctx = ExecutionContext(
            task_id="t1",
            title="T",
            description="D",
            execution=ExecutionSpec(type="code"),
            allowed_files=["a.py"],
            workspace_dir="/tmp/ws",
            branch="task/t1"
        )
        
        session = runtime.start_session(AgentProps(agent_id="a1", name="A1"), "/tmp/ws", {})
        runtime.send_execution_payload(session, context=ctx)
        result = runtime.wait_for_completion(session)
        
        assert result.success is True
        assert "Gemini output" in result.stdout
        # Check if environment variable was set
        args, kwargs = mock_run.call_args
        assert kwargs["env"]["GEMINI_API_KEY"] == "fake-key"
        assert "gemini" in args[0]
