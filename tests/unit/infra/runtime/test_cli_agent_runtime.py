import pytest
from unittest.mock import MagicMock, patch
from src.core.models import AgentProps, ExecutionContext, ExecutionSpec, AgentExecutionResult
from src.infra.runtime.agent_runtime import CliAgentRuntime

class TestCliAgentRuntime:
    @patch("subprocess.run")
    def test_run_command(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="output",
            stderr=""
        )
    
        # Patch abstract methods to allow instantiation
        with patch.multiple(CliAgentRuntime, __abstractmethods__=set()):
            runtime = CliAgentRuntime(api_key="fake", model="m")
            # Test public API instead of private _run
            handle = runtime.start_session(AgentProps(agent_id="a1", name="A1"), "/tmp", {})
            runtime.send_execution_payload(handle, ExecutionContext(
                task_id="t1", title="T", description="D", 
                execution=ExecutionSpec(type="t"), allowed_files=[], 
                workspace_dir="/tmp", branch="b"
            ))
            result = runtime.wait_for_completion(handle)
            
            assert result.success is True
            assert "output" in result.stdout
        mock_run.assert_called_once()
