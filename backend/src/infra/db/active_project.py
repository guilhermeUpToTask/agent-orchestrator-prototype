"""
src/infra/db/active_project.py — SQLite implementation of ActiveProjectPort.

Persists the per-session active-project selection. The CLI uses a single
implicit session id (``"cli"``); the API keys it per caller.
"""
from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from src.domain.ports.active_project import ActiveProjectPort
from src.infra.db._session import run_in_session
from src.infra.db.tables import ActiveProjectTable

CLI_SESSION = "cli"


class SqliteActiveProject(ActiveProjectPort):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def get_active(self, session_id: str) -> str | None:
        with self._sf() as s:
            row = s.get(ActiveProjectTable, session_id)
            return row.project_id if row else None

    def set_active(self, session_id: str, project_id: str) -> None:
        def _op(s: Session) -> None:
            row = s.get(ActiveProjectTable, session_id)
            if row is not None:
                row.project_id = project_id
            else:
                s.add(ActiveProjectTable(session_id=session_id, project_id=project_id))
        run_in_session(self._sf, _op)
