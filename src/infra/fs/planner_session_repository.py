"""
src/infra/fs/planner_session_repository.py — YAML filesystem adapter for PlannerSessionRepositoryPort.

One YAML file per session: planner_sessions/<session_id>.yaml
Follows the same atomic-write and quarantine patterns as YamlGoalRepository.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import yaml

from src.domain.aggregates.planner_session import PlannerSession
from src.domain.repositories.planner_session_repository import PlannerSessionRepositoryPort


class YamlPlannerSessionRepository(PlannerSessionRepositoryPort):
    """Stores PlannerSession aggregates as YAML files, one per session."""

    def __init__(self, sessions_dir: str | Path) -> None:
        self._dir = Path(sessions_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._quarantine = self._dir / "quarantine"
        self._quarantine.mkdir(exist_ok=True)

    def save(self, session: PlannerSession) -> None:
        self._atomic_write(self._session_path(session.session_id), session)

    def load(self, session_id: str) -> PlannerSession:
        path = self._session_path(session_id)
        if not path.exists():
            raise KeyError(f"PlannerSession '{session_id}' not found at {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return PlannerSession.model_validate(data)

    def list_all(self) -> list[PlannerSession]:
        """Return all sessions, newest first (by created_at)."""
        sessions: list[PlannerSession] = []
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                if data is None:
                    continue
                sessions.append(PlannerSession.model_validate(data))
            except Exception as exc:
                import structlog
                structlog.get_logger(__name__).error(
                    "planner_session_repo.corrupt_file_quarantined",
                    path=str(path),
                    error=str(exc),
                )
                try:
                    shutil.move(str(path), str(self._quarantine / path.name))
                except Exception:
                    pass
        sessions.sort(key=lambda s: s.created_at, reverse=True)
        return sessions

    def get(self, session_id: str) -> Optional[PlannerSession]:
        try:
            return self.load(session_id)
        except KeyError:
            return None

    # -- helpers --

    def _session_path(self, session_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
        return self._dir / f"{safe}.yaml"

    def _atomic_write(self, path: Path, session: PlannerSession) -> None:
        tmp = path.with_suffix(".tmp")
        try:
            data = session.model_dump(mode="json")
            tmp.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise


# ---------------------------------------------------------------------------
# In-memory adapter (tests / dry-run)
# ---------------------------------------------------------------------------

class InMemoryPlannerSessionRepository(PlannerSessionRepositoryPort):
    """Volatile in-memory adapter for tests and dry-run mode."""

    def __init__(self) -> None:
        self._store: dict[str, PlannerSession] = {}

    def save(self, session: PlannerSession) -> None:
        self._store[session.session_id] = session

    def load(self, session_id: str) -> PlannerSession:
        if session_id not in self._store:
            raise KeyError(f"PlannerSession '{session_id}' not found")
        return self._store[session_id]

    def list_all(self) -> list[PlannerSession]:
        sessions = list(self._store.values())
        sessions.sort(key=lambda s: s.created_at, reverse=True)
        return sessions

    def get(self, session_id: str) -> Optional[PlannerSession]:
        return self._store.get(session_id)
