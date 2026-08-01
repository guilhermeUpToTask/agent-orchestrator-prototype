"""Where the migrations are, in one place.

The migration directory ships INSIDE the package (`src/infra/db/migrations/`)
rather than beside it at the repository root, because an installed copy has no
repository. Resolving it from `Path(__file__).parents[3]` worked in every test
and every developer checkout and failed the moment the wheel was installed:
that path is `site-packages`, whose only `alembic` is the library, so alembic
was handed its own package as a migration directory and the database could
never be created (`test_migrations_are_packaged.py`).

`alembic.ini` stays at the backend root for `alembic` CLI use during
development. It is NOT read here: an installed copy has no ini file, so the
config is constructed instead. The ini's `script_location` points at this same
directory so the two cannot drift.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config


def migrations_dir() -> Path:
    """Derived from THIS module's location — never from a repository marker.

    A resolver that walked up looking for `pyproject.toml` or `alembic.ini`
    would pass from a checkout and fail once installed, which is exactly the
    failure this module exists to retire.
    """
    return Path(__file__).resolve().parent / "migrations"


def alembic_config(database_url: str) -> Config:
    """A ready-to-run alembic config for `database_url`, built in memory."""
    config = Config()
    config.set_main_option("script_location", str(migrations_dir()))
    config.set_main_option("sqlalchemy.url", database_url)
    return config
