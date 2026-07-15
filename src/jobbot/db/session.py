"""Async engine / session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str, echo: bool = False) -> AsyncEngine:
    """Create the global async engine + sessionmaker. Idempotent."""
    global _engine, _sessionmaker
    if _engine is None:
        if database_url.startswith("sqlite"):
            # SQLite: no connection pool sizing; a single file connection.
            _engine = create_async_engine(database_url, echo=echo)
        else:
            _engine = create_async_engine(
                database_url,
                echo=echo,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=10,
            )
        _sessionmaker = async_sessionmaker(
            _engine, expire_on_commit=False, class_=AsyncSession
        )
    return _engine


def dialect_name() -> str:
    """Name of the active DB dialect ('postgresql', 'sqlite', ...)."""
    if _engine is None:
        raise RuntimeError("Engine not initialized; call init_engine() first")
    return _engine.dialect.name


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Engine not initialized; call init_engine() first")
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional session context manager."""
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
