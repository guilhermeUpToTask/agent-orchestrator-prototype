from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import structlog

from src.domain import AgentExecutionResult
from src.domain import TaskLogsPort, TestRunnerPort
log = structlog.get_logger(__name__)

MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB per stream

#TODO: refactor this to be only logging system

def _default_log_base() -> Path:
    """Resolve logs_dir lazily so the module can be imported without side-effects."""
    from src.infra.settings.service import SettingsService
    from src.infra.project_paths import ProjectPaths
    ctx = SettingsService().load()
    return ProjectPaths.for_project(
        ctx.machine.orchestrator_home, ctx.machine.project_name
    ).logs_dir


# Module-level alias — tests patch this via monkeypatch.setattr or by
# constructing FilesystemTaskLogsAdapter(logs_base=tmp_path)
LOG_BASE: Path | None = None  # resolved on first use if not set explicitly


class FilesystemTaskLogsAdapter(TaskLogsPort):
    """
    Filesystem-backed implementation of TaskLogsPort.

    Args:
      logs_base: Directory under which per-task log subdirectories are created.
                 When not provided the global LOG_BASE is used (resolved lazily
                 from OrchestratorConfig). Prefer passing logs_base explicitly
                 so tests can use tmp_path without global state.
    """

    def __init__(self, logs_base: Path | None = None) -> None:
        self._logs_base = logs_base

    def save_logs(self, task_id: str, result: AgentExecutionResult) -> None:
        import src.infra.logs_and_tests as _m

        base = self._logs_base or _m.LOG_BASE or _default_log_base()
        log_dir = base / task_id
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "stdout.txt").write_text(result.stdout, encoding="utf-8")
            (log_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
            meta = {
                "exit_code": result.exit_code,
                "success": result.success,
                "elapsed_seconds": result.elapsed_seconds,
                "modified_files": result.modified_files,
            }
            (log_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("logs.save_failed", task_id=task_id, log_dir=str(log_dir), error=str(exc))


class SubprocessTestRunnerAdapter(TestRunnerPort):
    """
    Subprocess-backed implementation of TestRunnerPort.
    Mirrors the previous behaviour in WorkerHandler._run_tests.
    """

    def run_tests(self, workspace_path: str, test_command: str) -> None:
        log.info("tests.running", command=test_command, cwd=workspace_path)
        cmd_parts = shlex.split(test_command)
        try:
            proc = subprocess.run(
                cmd_parts,
                shell=False,
                cwd=workspace_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Test command not found: {cmd_parts[0]!r} — {exc}") from exc

        stdout = proc.stdout[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr = proc.stderr[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")

        if proc.returncode != 0:
            raise RuntimeError(
                f"Tests failed (exit {proc.returncode})\nstdout: {stdout}\nstderr: {stderr}"
            )
        log.info("tests.passed", command=test_command)
