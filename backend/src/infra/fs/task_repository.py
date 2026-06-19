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

import shutil
import threading
from pathlib import Path

import yaml

from src.domain import TaskAggregate
from src.domain import TaskRepositoryPort


class YamlTaskRepository(TaskRepositoryPort):
    """
    Stores tasks as discrete YAML files in a designated directory.
    Uses temp-file writes and os.replace() to guarantee atomic file updates on POSIX.

    Concurrency contract (Single Writer Process, Many Threads)
    ----------------------------------------------------------
    `update_if_version()` is a read-compare-write guarded by an in-process
    lock: it is safe for one process with any number of threads (the API
    process hosts the task manager, reconciler and HTTP handlers on
    separate threads). It is NOT atomic across processes — which is why
    workers never write task state: they publish task.execution_* events
    and the task manager applies the transitions (TaskRecordResultUseCase).
    If multi-process writers are ever needed, replace this repository with
    a true atomic CAS backend (Postgres or Redis Lua).
    """

    def __init__(self, tasks_dir: str | Path) -> None:
        self._dir = Path(tasks_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        # FIX #3.1: quarantine directory for corrupt task files
        self._quarantine = self._dir / "quarantine"
        self._quarantine.mkdir(exist_ok=True)
        self._write_lock = threading.Lock()

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
        with self._write_lock:
            path = self._task_path(task_id)
            if not path.exists():
                raise KeyError(f"Task {task_id} not found")

            current = yaml.safe_load(path.read_text())
            if current.get("state_version") != expected_version:
                return False  # version conflict

            self._atomic_write(path, new_state)
            return True

    def save(self, task: TaskAggregate) -> None:
        with self._write_lock:
            path = self._task_path(task.task_id)
            self._atomic_write(path, task)

    def append_history(
        self,
        task_id: str,
        event: str,
        actor: str,
        detail: dict,
    ) -> None:
        with self._write_lock:
            task = self.load(task_id)
            from src.domain import HistoryEntry

            task.history.append(HistoryEntry(event=event, actor=actor, detail=detail))
            self._atomic_write(self._task_path(task_id), task)

    def delete(self, task_id: str) -> bool:
        """Remove the task YAML file.  Returns True if deleted, False if not found."""
        path = self._task_path(task_id)
        if not path.exists():
            return False
        path.unlink()
        return True

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
        Delegates atomic write to AtomicFileWriter utility.
        """
        from src.infra.fs.atomic_writer import AtomicFileWriter

        data = task.model_dump(mode="json")
        content = yaml.dump(data, default_flow_style=False, allow_unicode=True)
        AtomicFileWriter.write_text(path, content)
