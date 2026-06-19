"""
src/infra/db/engine.py — SQLite engine + session factory.

This is the single place SQLite operational policy is applied. Every connection
gets the PRAGMAs the persist-first guarantee depends on:

  journal_mode=WAL      concurrent readers + one writer
  synchronous=FULL      commit implies fsync (durability == old file fsync)
  foreign_keys=ON       referential integrity (OFF by default in SQLite!)
  busy_timeout          'database is locked' waits instead of erroring instantly

The PRAGMAs are attached via a ``connect`` event listener so they apply to
pooled connections too, not just the first one.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

log = structlog.get_logger(__name__)

# Default filename for the single config/state database under orchestrator_home.
DB_FILENAME = "orchestrator.db"

# How long a writer waits on a locked DB before raising (ms). The adapter adds
# bounded application-level retries on top of this.
_BUSY_TIMEOUT_MS = 5000


def db_url_for_home(orchestrator_home: Path) -> str:
    """Return the SQLite URL for the database under ``orchestrator_home``."""
    orchestrator_home.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{orchestrator_home / DB_FILENAME}"


def _apply_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=FULL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    cursor.close()


def build_engine(db_url: str) -> Engine:
    """Create an engine with the durability/integrity PRAGMAs attached."""
    engine = create_engine(db_url, future=True)
    event.listen(engine, "connect", _apply_pragmas)
    log.info("db.engine_built", url=db_url)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to ``engine`` (no autoflush surprises)."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
