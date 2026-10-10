"""Async SQLAlchemy engine/session helpers."""

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the async Postgres engine."""
    return create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Bound session factory (expire_on_commit=False for worker use)."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def check_connectivity(settings: Settings) -> str:
    """Run SELECT 1; return 'ok' or raise."""
    engine = create_engine(settings)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()
    return "ok"


async def session_scope(settings: Settings) -> AsyncIterator[AsyncSession]:
    """Yield one session (for scripts/workers)."""
    engine = create_engine(settings)
    factory = session_factory(engine)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()
