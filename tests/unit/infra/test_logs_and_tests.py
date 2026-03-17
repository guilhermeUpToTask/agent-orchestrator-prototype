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

    def test_save_logs_handles_oserror_gracefully(self, monkeypatch, tmp_path):
        # Create a file where the directory would be — mkdir raises OSError
        blocker = tmp_path / "logs"
        blocker.write_text("i am a file not a dir")
        monkeypatch.setattr(logs_module, "LOG_BASE", blocker)

        from src.infra.logs_and_tests import FilesystemTaskLogsAdapter

        result = AgentExecutionResult(success=True, exit_code=0, stdout="x", stderr="")
        # Must not raise — OSError is caught and logged as a warning
        FilesystemTaskLogsAdapter().save_logs("task-abc", result)
