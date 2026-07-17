"""Async SQLAlchemy setup: engine, session factory, and migration runner."""
from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine() -> AsyncEngine:
    """Create (once) and return the global async engine."""
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.DATABASE_URL,
            echo=False,
            pool_size=5,          # min connections
            max_overflow=15,      # pool_size + max_overflow = 20 max connections
            pool_timeout=30,      # connection timeout: 30 seconds
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        _session_factory = async_sessionmaker(
            bind=_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields a database session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def close_db() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def run_migrations() -> None:
    """Run ``alembic upgrade head`` programmatically (used at app startup).

    Executed in a worker thread so the Alembic/synchronous machinery never
    blocks the asyncio event loop.
    """
    if not os.path.exists("alembic.ini"):
        logger.warning(
            "alembic.ini not found in cwd; skipping in-app migrations "
            "(migrations run via startup.sh)."
        )
        return

    def _upgrade() -> None:
        from alembic import command
        from alembic.config import Config

        cfg = Config("alembic.ini")
        cfg.set_main_option("script_location", "migrations")
        command.upgrade(cfg, "head")

    logger.info("Running Alembic migrations (upgrade head)")
    await asyncio.to_thread(_upgrade)
    logger.info("Alembic migrations complete")
