"""
src/domain/ports/active_project.py — Active-project selection port.

Tracks which project is "active" for a given session (a CLI invocation or an API
caller). The CLI uses a single implicit session; the API keys it per request.
Implementations persist the mapping (SQLite/global config); the domain only sees
this contract.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class ActiveProjectPort(ABC):
    @abstractmethod
    def get_active(self, session_id: str) -> str | None:
        """Return the active project id for the session, or None."""
        ...

    @abstractmethod
    def set_active(self, session_id: str, project_id: str) -> None:
        """Set the active project id for the session."""
        ...
