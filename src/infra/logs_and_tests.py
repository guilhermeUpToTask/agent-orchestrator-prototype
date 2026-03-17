from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import structlog

from src.domain import AgentExecutionResult
from src.domain import TaskLogsPort, TestRunnerPort
from src.infra.config import config

log = structlog.get_logger(__name__)

MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB per stream

# Module-level alias — tests patch this directly via monkeypatch.setattr
LOG_BASE = config.logs_dir


class FilesystemTaskLogsAdapter(TaskLogsPort):
    """
    Filesystem-backed implementation of TaskLogsPort.
    Mirrors the previous behaviour in WorkerHandler._save_logs.
    """

    def save_logs(self, task_id: str, result: AgentExecutionResult) -> None:
        import src.infra.logs_and_tests as _m

        log_dir = _m.LOG_BASE / task_id
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
