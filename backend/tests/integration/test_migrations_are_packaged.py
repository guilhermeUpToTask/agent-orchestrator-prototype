"""The migrations must travel with the code, or an installed copy has no schema.

Found by installing the built wheel into a clean venv (Phase 6's own exit
criterion — "install to green Tier 0 using public docs only"):

    $ pip install praxis_orchestrator-0.1.0-py3-none-any.whl
    $ praxis db upgrade
    ImportError: Can't find Python file .../site-packages/alembic/env.py

`db_upgrade` resolved the migration directory from `Path(__file__).parents[3]`,
which is the repository root in a source checkout and `site-packages` in an
installed one — where the only `alembic` present is the LIBRARY, so alembic was
handed its own package as a migration directory. The database could never be
created, and nothing in the suite noticed because every test ran from a
checkout.

These tests are about LOCATION, not schema: `test_migrations.py` already proves
the chain matches the metadata.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import praxis_orchestrator
from praxis_orchestrator.infra.db.migration_config import alembic_config, migrations_dir

pytestmark = pytest.mark.integration

PACKAGE_ROOT = Path(praxis_orchestrator.__file__).resolve().parent


def test_the_migration_directory_lives_inside_the_package() -> None:
    """The property that makes it survive `pip install`. Resolving from the
    repository root is what broke: an installed copy has no repository."""
    assert migrations_dir().is_relative_to(PACKAGE_ROOT)


def test_the_migration_directory_is_complete() -> None:
    directory = migrations_dir()

    assert (directory / "env.py").is_file()
    assert (directory / "versions").is_dir()
    assert list((directory / "versions").glob("*.py")), "no migration scripts"


def test_the_resolver_does_not_depend_on_a_repository_marker() -> None:
    """A resolver that walks up looking for `pyproject.toml` or `alembic.ini`
    would pass the two tests above from a checkout and still fail once
    installed. This asserts the path is derived from the MODULE's own location."""
    expected = Path(migration_config_module().__file__).resolve().parent / "migrations"

    assert migrations_dir() == expected


def migration_config_module():  # noqa: ANN201 - test helper
    from praxis_orchestrator.infra.db import migration_config

    return migration_config


def test_the_config_is_built_without_reading_a_file_outside_the_package() -> None:
    """`alembic.ini` stays in the repository for `alembic` CLI use, but an
    installed copy has no ini to read, so the config must be constructed."""
    config = alembic_config("sqlite:///example.db")

    assert config.get_main_option("script_location") == str(migrations_dir())
    assert config.get_main_option("sqlalchemy.url") == "sqlite:///example.db"
