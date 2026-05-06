"""Database engine and initialization."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy import text

from aihass.config import get_settings
from .models import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """Get or create the async database engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


async def init_db() -> None:
    """Create all tables and set up TimescaleDB hypertable.

    Safe to call multiple times — uses CREATE TABLE IF NOT EXISTS.
    """
    engine = get_engine()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Set up TimescaleDB hypertable for state_history (idempotent)
    async with engine.begin() as conn:
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"))
            await conn.execute(text(
                "SELECT create_hypertable('state_history', 'time', "
                "if_not_exists => TRUE, migrate_data => TRUE)"
            ))
            logger.info("TimescaleDB hypertable configured for state_history")
        except Exception as exc:
            # TimescaleDB may not be installed — fall back to plain PostgreSQL
            logger.warning(
                "TimescaleDB not available, using plain PostgreSQL for state_history: %s", exc
            )
