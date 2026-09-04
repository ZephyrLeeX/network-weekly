"""Alembic environment.

The database URL always comes from `backend.config.load_settings()`
(DATABASE_URL env var); no credentials live in alembic.ini.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the backend package importable regardless of how alembic is invoked
# (host CLI, container, pytest).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import load_settings  # noqa: E402
from backend.db import models  # noqa: E402, F401  (registers tables on Base.metadata)
from backend.db.base import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DBAPI)."""

    context.configure(
        url=load_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect with the configured URL and apply migrations."""

    configuration = config.get_section(config.config_ini_section, {}) or {}
    configuration["sqlalchemy.url"] = load_settings().database_url
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
