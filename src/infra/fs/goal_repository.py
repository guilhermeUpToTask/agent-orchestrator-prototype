"""
src/infra/fs/goal_repository.py — YAML filesystem adapter for GoalRepositoryPort.

One goal per YAML file: goals/<goal_id>.yaml

Follows the same atomic-write and optimistic-concurrency patterns as
YamlTaskRepository:
  - Writes go through a temp file + os.replace() (POSIX atomic rename)
  - update_if_version() checks state_version before writing
  - list_all() quarantines corrupt files to goals/quarantine/
"""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from src.domain.aggregates.goal import GoalAggregate
from src.domain.repositories.goal_repository import GoalRepositoryPort


class YamlGoalRepository(GoalRepositoryPort):
    """
    Stores GoalAggregates as discrete YAML files.

    Inherits the same single-orchestrator CAS constraint as YamlTaskRepository:
    safe as long as exactly one orchestrator process runs per project.
    """

    def __init__(self, goals_dir: str | Path | None = None) -> None:
        if goals_dir is None:
            from src.infra.config import config
            goals_dir = config.goals_dir
        self._dir = Path(goals_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._quarantine = self._dir / "quarantine"
        self._quarantine.mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # GoalRepositoryPort
    # ------------------------------------------------------------------

    def save(self, goal: GoalAggregate) -> None:
        self._atomic_write(self._goal_path(goal.goal_id), goal)

    def load(self, goal_id: str) -> GoalAggregate:
        path = self._goal_path(goal_id)
        if not path.exists():
            raise KeyError(f"Goal '{goal_id}' not found at {path}")
        data = yaml.safe_load(path.read_text())
        return GoalAggregate.model_validate(data)

    def update_if_version(
        self,
        goal_id: str,
        new_state: GoalAggregate,
        expected_version: int,
    ) -> bool:
        path = self._goal_path(goal_id)
        if not path.exists():
            raise KeyError(f"Goal '{goal_id}' not found")
        current = yaml.safe_load(path.read_text())
        if current.get("state_version") != expected_version:
            return False
        self._atomic_write(path, new_state)
        return True

    def list_all(self) -> list[GoalAggregate]:
        goals = []
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text())
                if data is None:
                    continue
                goals.append(GoalAggregate.model_validate(data))
            except Exception as exc:
                import structlog
                structlog.get_logger(__name__).error(
                    "goal_repo.corrupt_file_quarantined",
                    path=str(path),
                    error=str(exc),
                )
                dest = self._quarantine / path.name
                try:
                    shutil.move(str(path), str(dest))
                except OSError:
                    pass
        return goals

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _goal_path(self, goal_id: str) -> Path:
        return self._dir / f"{goal_id}.yaml"

    def _atomic_write(self, path: Path, goal: GoalAggregate) -> None:
        from src.infra.fs.atomic_writer import AtomicFileWriter
        data = goal.model_dump(mode="json")
        content = yaml.dump(data, default_flow_style=False, allow_unicode=True)
        AtomicFileWriter.write_text(path, content)
