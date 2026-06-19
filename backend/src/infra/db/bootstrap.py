"""
src/infra/db/bootstrap.py — config DB wiring.

Single entry point that runs Alembic migrations (schema is always versioned —
never ``create_all`` on a populated schema) and returns the engine + session
factory bound to the database under ``orchestrator_home``.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.infra.db.engine import build_engine, db_url_for_home, make_session_factory

log = structlog.get_logger(__name__)

# Repo-root alembic.ini + scripts dir (three levels up from src/infra/db/).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_ALEMBIC_DIR = _REPO_ROOT / "alembic"


def run_migrations(db_url: str) -> None:
    """Upgrade the database at ``db_url`` to head.

    ``script_location`` is pinned to an absolute path so migrations run
    correctly regardless of the process working directory.
    """
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")
    log.info("db.migrated", url=db_url)


@lru_cache(maxsize=8)
def _engine_for_url(db_url: str) -> Engine:
    run_migrations(db_url)
    return build_engine(db_url)


def config_db(orchestrator_home: Path) -> tuple[Engine, sessionmaker[Session]]:
    """Return (engine, session_factory) for the config DB, migrated to head."""
    url = db_url_for_home(orchestrator_home)
    engine = _engine_for_url(url)
    return engine, make_session_factory(engine)
