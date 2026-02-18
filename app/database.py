"""Async SQLAlchemy database engine and session management.

Provides async database connectivity with automatic SQLite directory creation
and FastAPI dependency injection support.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.base import Base
from app.config import settings


def _ensure_database_directory() -> None:
    """Ensure the SQLite database parent directory exists.

    This is a no-op for non-SQLite databases or in-memory databases.
    """
    url = make_url(settings.database_url)

    if not url.drivername.startswith("sqlite"):
        return

    database = url.database
    if database in (None, "", ":memory:"):
        return

    db_path = Path(database).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)


# Ensure database directory exists before creating engine
_ensure_database_directory()


# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable foreign key constraints for SQLite connections."""
    if "sqlite" in str(type(dbapi_connection)):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# Async session factory
AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session.

    Note: Transaction control (commit/rollback) should be handled
    explicitly in the service layer for proper error handling.
    """
    async with AsyncSessionFactory() as session:
        yield session


async def init_db() -> None:
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
