"""The fresh Alembic chain must produce the same schema the ORM metadata
declares (the test harness uses metadata.create_all; production uses
`alembic upgrade head` — they must never drift)."""
from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from src.infra.db.tables import Base

BACKEND_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration


def _columns(engine, table: str) -> dict[str, bool]:
    """name -> nullable, for drift comparison."""
    return {c["name"]: bool(c["nullable"]) for c in inspect(engine).get_columns(table)}


def test_alembic_upgrade_head_matches_metadata(tmp_path):
    # migrate one db
    migrated_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", migrated_url)
    command.upgrade(cfg, "head")

    # create_all another
    created_url = f"sqlite:///{tmp_path / 'created.db'}"
    created_engine = create_engine(created_url)
    Base.metadata.create_all(created_engine)

    migrated_engine = create_engine(migrated_url)
    migrated_tables = set(inspect(migrated_engine).get_table_names()) - {
        "alembic_version"
    }
    created_tables = set(inspect(created_engine).get_table_names())
    assert migrated_tables == created_tables

    for table in sorted(created_tables):
        assert _columns(migrated_engine, table) == _columns(created_engine, table), (
            f"schema drift in table {table!r}"
        )
