import pytest
from src.domain import AgentProps, ExecutionContext, ExecutionSpec
from src.infra.runtime.dry_run_runtime import SimulatedAgentRuntime

class TestSimulatedAgentRuntime:
    def test_run_task_success(self):
        runtime = SimulatedAgentRuntime()
        ctx = ExecutionContext(
            task_id="t1",
            title="T",
            description="D",
            execution=ExecutionSpec(type="code"),
            allowed_files=[],
            workspace_dir="/tmp",
            branch="b"
        )
        session = runtime.start_session(AgentProps(agent_id="a1", name="A1"), "/tmp", {})
        runtime.send_execution_payload(session, ctx)
        result = runtime.wait_for_completion(session)
        assert result.success is True
        assert result.exit_code == 0

    def test_run_task_failure_on_fail_keyword(self):
        runtime = SimulatedAgentRuntime(simulate_failure=True)
        ctx = ExecutionContext(
            task_id="t1",
            title="FAIL THIS TASK",
            description="D",
            execution=ExecutionSpec(type="code"),
            allowed_files=[],
            workspace_dir="/tmp",
            branch="b"
        )
        session = runtime.start_session(AgentProps(agent_id="a1", name="A1"), "/tmp", {})
        runtime.send_execution_payload(session, ctx)
        result = runtime.wait_for_completion(session)
        assert result.success is False
        assert result.exit_code == 1
        assert "Simulated failure" in result.stderr
