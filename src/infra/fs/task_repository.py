"""
src/infra/fs/task_repository.py — YAML filesystem adapter for TaskRepositoryPort.

Atomic writes: write to .tmp file, fsync file, fsync parent directory, rename.
Version conflict detection: compare state_version before writing.

Fixes applied vs v1:
  #1.3  _atomic_write now fsyncs the parent directory entry after os.replace()
        so the rename survives a crash on ext4 / XFS in data=ordered mode.
  #3.1  list_all() quarantines corrupt files to tasks/quarantine/ instead of
        silently skipping them, making data loss and format regressions visible.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from src.core.models import TaskAggregate
from src.core.ports import TaskRepositoryPort


class YamlTaskRepository(TaskRepositoryPort):

    def __init__(self, tasks_dir: str | Path = "workflow/tasks") -> None:
        self._dir = Path(tasks_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        # FIX #3.1: quarantine directory for corrupt task files
        self._quarantine = self._dir / "quarantine"
        self._quarantine.mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # Port implementation
    # ------------------------------------------------------------------

    def load(self, task_id: str) -> TaskAggregate:
        path = self._task_path(task_id)
        if not path.exists():
            raise KeyError(f"Task {task_id} not found at {path}")
        data = yaml.safe_load(path.read_text())
        return TaskAggregate.model_validate(data)

    def update_if_version(
        self,
        task_id: str,
        new_state: TaskAggregate,
        expected_version: int,
    ) -> bool:
        path = self._task_path(task_id)
        if not path.exists():
            raise KeyError(f"Task {task_id} not found")

        current = yaml.safe_load(path.read_text())
        if current.get("state_version") != expected_version:
            return False  # version conflict

        self._atomic_write(path, new_state)
        return True

    def save(self, task: TaskAggregate) -> None:
        path = self._task_path(task.task_id)
        self._atomic_write(path, task)

    def append_history(
        self,
        task_id: str,
        event: str,
        actor: str,
        detail: dict,
    ) -> None:
        task = self.load(task_id)
        from src.core.models import HistoryEntry
        task.history.append(HistoryEntry(event=event, actor=actor, detail=detail))
        self._atomic_write(self._task_path(task_id), task)

    def list_all(self) -> list[TaskAggregate]:
        """
        Return all tasks in the tasks directory.

        FIX #3.1: Files that cannot be parsed are moved to tasks/quarantine/
        rather than being silently skipped. This surfaces data-loss and format
        regressions visibly instead of causing tasks to disappear from the
        reconciler's view.
        """
        tasks = []
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text())
                if data is None:
                    continue  # skip genuinely empty files (race with atomic write)
                tasks.append(TaskAggregate.model_validate(data))
            except Exception as exc:
                import structlog
                structlog.get_logger(__name__).error(
                    "fs_repo.corrupt_file_quarantined",
                    path=str(path),
                    error=str(exc),
                )
                # FIX #3.1: Move to quarantine so the problem is obvious
                dest = self._quarantine / path.name
                try:
                    shutil.move(str(path), str(dest))
                except OSError as move_err:
                    structlog.get_logger(__name__).warning(
                        "fs_repo.quarantine_move_failed",
                        path=str(path),
                        error=str(move_err),
                    )
        return tasks

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _task_path(self, task_id: str) -> Path:
        return self._dir / f"{task_id}.yaml"

    def _atomic_write(self, path: Path, task: TaskAggregate) -> None:
        """
        Write to a .tmp file, fsync it, rename it (POSIX atomic), then
        fsync the parent directory entry.

        FIX #1.3: The fsync on os.open(parent_dir) ensures the directory
        entry for the renamed file is flushed to disk.  Without it, a crash
        immediately after os.replace() can leave the directory pointing to
        the old inode on ext4 / XFS in data=ordered mode.
        """
        data = task.model_dump(mode="json")
        content = yaml.dump(data, default_flow_style=False, allow_unicode=True)

        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())   # fsync the file data
            os.replace(tmp_path, path)

            # FIX #1.3: fsync the directory so the rename is durable
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise