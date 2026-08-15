"""Alembic migration environment for the orchestrator config/state DB.

The database URL is resolved (in priority order):
  1. ``sqlalchemy.url`` set programmatically on the Config (bootstrap path),
  2. the ``PRAXIS_DB_URL`` environment variable (or the pre-rename
     ``ORCHESTRATOR_DB_URL``, via ``infra/env_compat.py``),
  3. the static placeholder in alembic.ini.
"""
from __future__ import annotations


from praxis_orchestrator.infra.env_compat import env
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from praxis_orchestrator.infra.db.tables import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# A URL set programmatically on the Config (bootstrap path) wins. Otherwise
# fall back to PRAXIS_DB_URL, then the alembic.ini placeholder.
if config.get_main_option("sqlalchemy.url") in (None, "", "sqlite:///orchestrator.db"):
    _env_url = env("PRAXIS_DB_URL")
    if _env_url:
        config.set_main_option("sqlalchemy.url", _env_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
