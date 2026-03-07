"""
src/infra/fs/task_repository.py — YAML filesystem adapter for TaskRepositoryPort.

Atomic writes: write to .tmp file, fsync, rename (POSIX atomic).
Version conflict detection: compare state_version before writing.
"""
from __future__ import annotations

import json
import os
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
        tasks = []
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text())
                if data is None:
                    continue  # skip empty files
                tasks.append(TaskAggregate.model_validate(data))
            except Exception as exc:
                import structlog
                structlog.get_logger(__name__).warning(
                    "fs_repo.load_error", path=str(path), error=str(exc)
                )
        return tasks

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _task_path(self, task_id: str) -> Path:
        return self._dir / f"{task_id}.yaml"

    def _atomic_write(self, path: Path, task: TaskAggregate) -> None:
        """Write to .tmp then rename (atomic on POSIX)."""
        data = task.model_dump(mode="json")
        content = yaml.dump(data, default_flow_style=False, allow_unicode=True)

        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise