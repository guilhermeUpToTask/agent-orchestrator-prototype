"""
src/infra/db/_session.py — shared transactional runner with lock-retry.

Single place the SQLite write policy lives: open a session, run the unit of
work, commit (durable under synchronous=FULL), and retry the transient
``database is locked`` OperationalError with bounded backoff before surfacing
InfrastructureError. Used by every SQLite adapter so config, task, secret,
and active-project writes share identical durability/contention behaviour.
"""
from __future__ import annotations

import time
from typing import Callable, TypeVar

import structlog
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from src.infra.errors import InfrastructureError

log = structlog.get_logger(__name__)

_T = TypeVar("_T")

MAX_LOCK_RETRIES = 5
LOCK_BACKOFF_BASE = 0.05  # seconds


def _is_locked(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


def run_in_session(session_factory: sessionmaker[Session], fn: Callable[[Session], _T]) -> _T:
    """Run ``fn`` in a committed transaction, retrying transient lock errors."""
    last: OperationalError | None = None
    for attempt in range(MAX_LOCK_RETRIES):
        try:
            with session_factory() as session:
                result = fn(session)
                session.commit()
                return result
        except OperationalError as exc:
            if not _is_locked(exc):
                raise
            last = exc
            time.sleep(LOCK_BACKOFF_BASE * (2 ** attempt))
            log.warning("db.locked_retry", attempt=attempt)
    raise InfrastructureError(
        "Database stayed locked beyond retry budget", code="DB_LOCKED"
    ) from last
