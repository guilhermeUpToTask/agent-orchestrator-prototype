"""
tests/unit/infra/runtime/test_pi_runtime.py — Unit tests for PiAgentRuntime.

Follows the same pattern as test_gemini_runtime.py and test_claude_code_runtime.py.
"""
import pytest
from unittest.mock import MagicMock, patch

from src.domain import AgentProps, ExecutionContext, ExecutionSpec
from src.infra.runtime.pi_runtime import PiAgentRuntime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(**overrides) -> ExecutionContext:
    defaults = dict(
        task_id="task-pi-001",
        title="Add logging middleware",
        description="Implement structured logging for all HTTP requests.",
        execution=ExecutionSpec(type="code:backend"),
        allowed_files=["src/middleware/logging.py"],
        workspace_dir="/tmp/ws-pi",
        branch="task/task-pi-001",
    )
    defaults.update(overrides)
    return ExecutionContext(**defaults)


def _make_agent(**overrides) -> AgentProps:
    defaults = dict(agent_id="pi-worker-001", name="Pi Worker")
    defaults.update(overrides)
    return AgentProps(**defaults)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestPiAgentRuntimeInit:
    def test_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key"):
            PiAgentRuntime(api_key="")

    def test_error_message_includes_env_var_name(self):
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
            PiAgentRuntime(api_key="", backend="openrouter")

    def test_invalid_backend_raises(self):
        with pytest.raises(ValueError, match="invalid-backend"):
            PiAgentRuntime(api_key="key", backend="invalid-backend")  # type: ignore

    def test_log_prefix(self):
        assert PiAgentRuntime(api_key="key").log_prefix == "pi"

    def test_default_model(self):
        assert PiAgentRuntime(api_key="key")._model == PiAgentRuntime.DEFAULT_MODEL

    def test_default_backend(self):
        r = PiAgentRuntime(api_key="key")
        assert r._backend == "anthropic"
        assert r._env_var == "ANTHROPIC_API_KEY"

    def test_gemini_backend_env_var(self):
        r = PiAgentRuntime(api_key="key", backend="gemini")
        assert r._env_var == "GEMINI_API_KEY"

    def test_openrouter_backend_env_var(self):
        r = PiAgentRuntime(api_key="key", backend="openrouter")
        assert r._env_var == "OPENROUTER_API_KEY"


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

class TestPiAgentRuntimeSession:
    @patch("subprocess.run")
    def test_run_task_calls_pi_cli(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="pi output\nTask complete",
            stderr="",
        )

        runtime = PiAgentRuntime(api_key="fake-anthropic-key")
        ctx = _make_context()

        session = runtime.start_session(_make_agent(), "/tmp/ws-pi", {})
        runtime.send_execution_payload(session, context=ctx)
        result = runtime.wait_for_completion(session)

        assert result.success is True
        assert "pi output" in result.stdout

        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert cmd[0] == "pi"
        assert "-p" in cmd

    @patch("subprocess.run")
    def test_injects_api_key_into_correct_env_var(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        for backend, expected_var in [
            ("anthropic", "ANTHROPIC_API_KEY"),
            ("gemini", "GEMINI_API_KEY"),
            ("openrouter", "OPENROUTER_API_KEY"),
        ]:
            runtime = PiAgentRuntime(api_key="test-key", backend=backend)
            session = runtime.start_session(_make_agent(), "/tmp/ws", {})
            runtime.send_execution_payload(session, context=_make_context())
            runtime.wait_for_completion(session)
            _, kwargs = mock_run.call_args
            assert kwargs["env"][expected_var] == "test-key"

    @patch("subprocess.run")
    def test_api_key_stored_on_runtime(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runtime = PiAgentRuntime(api_key="gemini-key-xyz", backend="gemini")
        assert runtime._api_key == "gemini-key-xyz"


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------

class TestPiAgentRuntimeBuildCmd:
    @patch("subprocess.run")
    def test_default_model_not_passed_as_flag(self, mock_run):
        """When using the default model, --model should be omitted."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        runtime = PiAgentRuntime(api_key="key", model=PiAgentRuntime.DEFAULT_MODEL)
        session = runtime.start_session(_make_agent(), "/tmp/ws", {})
        runtime.send_execution_payload(session, context=_make_context())
        runtime.wait_for_completion(session)

        args, _ = mock_run.call_args
        cmd = args[0]
        assert "--model" not in cmd

    @patch("subprocess.run")
    def test_non_default_model_passed_as_flag(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        runtime = PiAgentRuntime(api_key="key", model="gemini-2.0-flash")
        session = runtime.start_session(_make_agent(), "/tmp/ws", {})
        runtime.send_execution_payload(session, context=_make_context())
        runtime.wait_for_completion(session)

        args, _ = mock_run.call_args
        cmd = args[0]
        assert "--model" in cmd
        assert "gemini-2.0-flash" in cmd

    @patch("subprocess.run")
    def test_extra_flags_appended_to_cmd(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        runtime = PiAgentRuntime(
            api_key="key",
            extra_flags=["-e", "/path/to/extension.ts"],
        )
        session = runtime.start_session(_make_agent(), "/tmp/ws", {})
        runtime.send_execution_payload(session, context=_make_context())
        runtime.wait_for_completion(session)

        args, _ = mock_run.call_args
        cmd = args[0]
        assert "-e" in cmd
        assert "/path/to/extension.ts" in cmd


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

class TestPiAgentRuntimeBuildPrompt:
    def _build(self, **ctx_overrides) -> str:
        runtime = PiAgentRuntime(api_key="key")
        ctx = _make_context(**ctx_overrides)
        session = runtime.start_session(_make_agent(), "/tmp/ws", {})
        runtime.send_execution_payload(session, context=ctx)
        return session.prompt

    def test_prompt_contains_task_title(self):
        prompt = self._build(title="Refactor auth module")
        assert "Refactor auth module" in prompt

    def test_prompt_contains_description(self):
        prompt = self._build(description="Implement OAuth2 flow.")
        assert "Implement OAuth2 flow." in prompt

    def test_prompt_contains_allowed_files(self):
        prompt = self._build(allowed_files=["src/auth.py", "src/tokens.py"])
        assert "src/auth.py" in prompt
        assert "src/tokens.py" in prompt

    def test_prompt_contains_task_id_and_branch(self):
        prompt = self._build(task_id="task-abc-123", branch="task/task-abc-123")
        assert "task-abc-123" in prompt

    def test_prompt_contains_acceptance_criteria(self):
        prompt = self._build(
            execution=ExecutionSpec(
                type="code",
                acceptance_criteria=["All tests pass", "No linting errors"],
            )
        )
        assert "All tests pass" in prompt
        assert "No linting errors" in prompt

    def test_prompt_contains_test_command(self):
        prompt = self._build(
            execution=ExecutionSpec(
                type="code",
                test_command="pytest tests/unit -x",
            )
        )
        assert "pytest tests/unit -x" in prompt

    def test_prompt_contains_constraints(self):
        prompt = self._build(
            execution=ExecutionSpec(
                type="code",
                constraints={"max_lines": 100, "language": "Python"},
            )
        )
        assert "max_lines" in prompt
        assert "100" in prompt

    def test_no_criteria_section_when_empty(self):
        prompt = self._build(execution=ExecutionSpec(type="code"))
        assert "Acceptance criteria" not in prompt

    def test_no_verification_section_when_no_test_command(self):
        prompt = self._build(execution=ExecutionSpec(type="code"))
        assert "Verification" not in prompt


# ---------------------------------------------------------------------------
# Failure & timeout
# ---------------------------------------------------------------------------

class TestPiAgentRuntimeFailures:
    @patch("subprocess.run")
    def test_non_zero_exit_returns_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="partial output",
            stderr="error: something went wrong",
        )

        runtime = PiAgentRuntime(api_key="key")
        session = runtime.start_session(_make_agent(), "/tmp/ws", {})
        runtime.send_execution_payload(session, context=_make_context())
        result = runtime.wait_for_completion(session)

        assert result.success is False
        assert result.exit_code == 1
        assert "error: something went wrong" in result.stderr

    @patch("subprocess.run")
    def test_timeout_returns_failure_result(self, mock_run):
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(cmd=["pi"], timeout=1)

        runtime = PiAgentRuntime(api_key="key")
        session = runtime.start_session(_make_agent(), "/tmp/ws", {})
        runtime.send_execution_payload(session, context=_make_context())
        result = runtime.wait_for_completion(session, timeout_seconds=1)

        assert result.success is False
        assert result.exit_code == -1
        assert "TIMEOUT" in result.stderr


# ---------------------------------------------------------------------------
# Factory integration
# ---------------------------------------------------------------------------

class TestPiRuntimeFactory:
    def test_factory_builds_pi_runtime_anthropic_backend(self):
        from unittest.mock import patch
        from pydantic import SecretStr
        from src.infra.factory import build_agent_runtime
        import src.infra.factory as factory_module

        with patch.object(factory_module.app_config, "mode", "real"), \
             patch.object(factory_module.app_config, "anthropic_api_key", SecretStr("ant-key")):
            agent = AgentProps(
                agent_id="pi-001",
                name="Pi",
                runtime_type="pi",
                runtime_config={"model": "claude-sonnet-4-5", "backend": "anthropic"},
            )
            runtime = build_agent_runtime(agent)

        assert isinstance(runtime, PiAgentRuntime)
        assert runtime._model == "claude-sonnet-4-5"
        assert runtime._api_key == "ant-key"

    def test_factory_builds_pi_runtime_openrouter_backend(self):
        from unittest.mock import patch
        from pydantic import SecretStr
        from src.infra.factory import build_agent_runtime
        import src.infra.factory as factory_module

        with patch.object(factory_module.app_config, "mode", "real"), \
             patch.object(factory_module.app_config, "openrouter_api_key", SecretStr("sk-or-key")):
            agent = AgentProps(
                agent_id="pi-003",
                name="Pi OpenRouter",
                runtime_type="pi",
                runtime_config={"model": "anthropic/claude-sonnet-4-5", "backend": "openrouter"},
            )
            runtime = build_agent_runtime(agent)

        assert isinstance(runtime, PiAgentRuntime)
        assert runtime._backend == "openrouter"
        assert runtime._env_var == "OPENROUTER_API_KEY"
        assert runtime._api_key == "sk-or-key"
        from unittest.mock import patch
        from pydantic import SecretStr
        from src.infra.factory import build_agent_runtime
        import src.infra.factory as factory_module

        with patch.object(factory_module.app_config, "mode", "real"), \
             patch.object(factory_module.app_config, "gemini_api_key", SecretStr("gm-key")):
            agent = AgentProps(
                agent_id="pi-002",
                name="Pi Gemini",
                runtime_type="pi",
                runtime_config={"model": "gemini-2.0-flash", "backend": "gemini"},
            )
            runtime = build_agent_runtime(agent)

        assert isinstance(runtime, PiAgentRuntime)
        assert runtime._model == "gemini-2.0-flash"
        assert runtime._api_key == "gm-key"