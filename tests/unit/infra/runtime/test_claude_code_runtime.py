from unittest.mock import MagicMock, patch
from src.domain import AgentProps, ExecutionContext, ExecutionSpec
from src.infra.runtime.claude_code_runtime import ClaudeCodeRuntime

class TestClaudeCodeRuntime:
    @patch("subprocess.run")
    def test_run_task_calls_claude_cli(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"Claude output\nModified: b.py",
            stderr=b""
        )
        
        runtime = ClaudeCodeRuntime(api_key="fake-claude-key")
        ctx = ExecutionContext(
            task_id="t2",
            title="T",
            description="D",
            execution=ExecutionSpec(type="code"),
            allowed_files=["b.py"],
            workspace_dir="/tmp/ws2",
            branch="task/t2"
        )
        
        session = runtime.start_session(AgentProps(agent_id="a1", name="A1"), "/tmp/ws2", {})
        runtime.send_execution_payload(session, context=ctx)
        result = runtime.wait_for_completion(session)
        
        assert result.success is True
        assert "Claude output" in result.stdout
        args, kwargs = mock_run.call_args
        assert kwargs["env"]["ANTHROPIC_API_KEY"] == "fake-claude-key"
        assert "claude" in args[0]
