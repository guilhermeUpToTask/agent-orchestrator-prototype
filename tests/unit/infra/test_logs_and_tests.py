import json
import pytest
from pathlib import Path
from src.infra.logs_and_tests import FilesystemTaskLogsAdapter, SubprocessTestRunnerAdapter
from src.core.models import AgentExecutionResult

class TestFilesystemTaskLogsAdapter:
    def test_save_logs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
        # Re-import or re-initialize to pick up the env var if it's used at module level
        # Actually LOG_BASE is computed at module level in logs_and_tests.py
        # We need to monkeypatch the LOG_BASE in the module
        import src.infra.logs_and_tests
        monkeypatch.setattr(src.infra.logs_and_tests, "LOG_BASE", tmp_path / "logs")
        
        adapter = FilesystemTaskLogsAdapter()
        result = AgentExecutionResult(
            success=True,
            exit_code=0,
            stdout="hello",
            stderr="world",
            elapsed_seconds=1.5,
            modified_files=["a.py"]
        )
        
        adapter.save_logs("task-123", result)
        
        log_dir = tmp_path / "logs" / "task-123"
        assert (log_dir / "stdout.txt").read_text() == "hello"
        assert (log_dir / "stderr.txt").read_text() == "world"
        
        meta = json.loads((log_dir / "metadata.json").read_text())
        assert meta["exit_code"] == 0
        assert meta["success"] is True
        assert meta["elapsed_seconds"] == 1.5
        assert meta["modified_files"] == ["a.py"]

class TestSubprocessTestRunnerAdapter:
    def test_run_tests_success(self, tmp_path):
        adapter = SubprocessTestRunnerAdapter()
        # Use 'echo' as a successful command
        adapter.run_tests(str(tmp_path), "echo success")

    def test_run_tests_failure(self, tmp_path):
        adapter = SubprocessTestRunnerAdapter()
        # Use 'exit 1' as a failing command
        with pytest.raises(RuntimeError, match="Tests failed"):
            adapter.run_tests(str(tmp_path), "false")

    def test_run_tests_not_found(self, tmp_path):
        adapter = SubprocessTestRunnerAdapter()
        with pytest.raises(RuntimeError, match="Test command not found"):
            adapter.run_tests(str(tmp_path), "nonexistent-command-999")
