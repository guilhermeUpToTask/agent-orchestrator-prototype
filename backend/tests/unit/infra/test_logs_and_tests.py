import src.infra.logs_and_tests as logs_module
from src.domain import AgentExecutionResult


class TestFilesystemTaskLogsAdapter:
    def test_saves_logs_to_log_base(self, monkeypatch, tmp_path):
        monkeypatch.setattr(logs_module, "LOG_BASE", tmp_path / "logs")

        from src.infra.logs_and_tests import FilesystemTaskLogsAdapter

        result = AgentExecutionResult(
            success=True,
            exit_code=0,
            stdout="hello",
            stderr="",
            elapsed_seconds=1.0,
        )
        FilesystemTaskLogsAdapter().save_logs("task-123", result)

        log_dir = tmp_path / "logs" / "task-123"
        assert (log_dir / "stdout.txt").read_text() == "hello"
        assert (log_dir / "stderr.txt").exists()
        assert (log_dir / "metadata.json").exists()

    def test_read_logs_round_trips_saved_logs(self, monkeypatch, tmp_path):
        monkeypatch.setattr(logs_module, "LOG_BASE", tmp_path / "logs")
        from src.infra.logs_and_tests import FilesystemTaskLogsAdapter

        adapter = FilesystemTaskLogsAdapter()
        adapter.save_logs("task-r", AgentExecutionResult(
            success=False, exit_code=1, stdout="out", stderr="err",
            elapsed_seconds=2.5, modified_files=["a.py"],
        ))
        logs = adapter.read_logs("task-r")
        assert logs == {
            "stdout": "out", "stderr": "err", "exit_code": 1, "success": False,
            "elapsed_seconds": 2.5, "modified_files": ["a.py"],
        }

    def test_read_logs_returns_none_when_absent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(logs_module, "LOG_BASE", tmp_path / "logs")
        from src.infra.logs_and_tests import FilesystemTaskLogsAdapter
        assert FilesystemTaskLogsAdapter().read_logs("nope") is None

    def test_save_logs_handles_oserror_gracefully(self, monkeypatch, tmp_path):
        # Create a file where the directory would be — mkdir raises OSError
        blocker = tmp_path / "logs"
        blocker.write_text("i am a file not a dir")
        monkeypatch.setattr(logs_module, "LOG_BASE", blocker)

        from src.infra.logs_and_tests import FilesystemTaskLogsAdapter

        result = AgentExecutionResult(success=True, exit_code=0, stdout="x", stderr="")
        # Must not raise — OSError is caught and logged as a warning
        FilesystemTaskLogsAdapter().save_logs("task-abc", result)
