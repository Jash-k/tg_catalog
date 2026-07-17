"""Alembic environment config (async, asyncpg)."""
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Make the app package importable when running `alembic` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings  # noqa: E402
from app.database import Base  # noqa: E402
from app.models import catalog as _catalog_models  # noqa: E402,F401 - register models
from app.models import enums as _enum_models  # noqa: E402,F401
from app.utils.helpers import mask_database_url  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    # get_settings() already normalizes the scheme and composes the URL from
    # Railway's PGHOST/PGUSER/... variables when DATABASE_URL is unset.
    raw = os.environ.get("DATABASE_URL") or get_settings().DATABASE_URL
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://"):]
    if raw.startswith("postgresql://"):
        raw = "postgresql+asyncpg://" + raw[len("postgresql://"):]
    if "user:pass@host" in raw:
        # .env.example placeholder leaked into production - spell it out.
        raw = get_settings().DATABASE_URL
    return raw


_resolved_url = _database_url()
print(f"[alembic] connecting to {mask_database_url(_resolved_url)}")
config.set_main_option("sqlalchemy.url", _resolved_url)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
