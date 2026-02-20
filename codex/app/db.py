"""
Database connection management using SQLModel with async SQLite.

Provides async database engine, session management, and initialization
for the Miniflux -> Discord relay service.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


# Module-level engine and session factory (initialized lazily)
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine() -> AsyncEngine:
    """
    Get or create the async database engine.

    The engine is created on first access and reused for subsequent calls.
    This lazy initialization allows the module to be imported without
    immediately creating a database connection.

    Returns:
        AsyncEngine: The SQLAlchemy async engine instance.
    """
    global _engine

    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.is_development and settings.log_level == "DEBUG",
            future=True,
            # Connection pool settings for SQLite
            pool_pre_ping=True,
        )

    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    Get or create the async session factory.

    Returns:
        async_sessionmaker: Factory for creating AsyncSession instances.
    """
    global _session_factory

    if _session_factory is None:
        engine = _get_engine()
        _session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    return _session_factory


async def init_db() -> None:
    """
    Initialize the database by creating all tables.

    This function should be called during application startup (e.g., in
    FastAPI lifespan). It imports all models to ensure they are registered
    with SQLModel's metadata before creating tables.

    Example:
        ```python
        from contextlib import asynccontextmanager
        from fastapi import FastAPI
        from app.db import init_db

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            await init_db()
            yield

        app = FastAPI(lifespan=lifespan)
        ```
    """
    # Import models to register them with SQLModel metadata
    # These imports are intentionally placed here to avoid circular imports
    from app.models.route_rule import RouteRule  # noqa: F401
    from app.models.seen_entry import SeenEntry  # noqa: F401
    from app.models.sync_state import SyncState  # noqa: F401
    from app.models.push_log import PushLog  # noqa: F401

    engine = _get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """
    FastAPI dependency that provides an async database session.

    This is an async generator that yields a session and ensures proper
    cleanup after the request is complete. Use with FastAPI's Depends():

    Example:
        ```python
        from fastapi import Depends
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.db import get_session

        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_session)):
            ...
        ```

    Yields:
        AsyncSession: An async database session instance.
    """
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def reset_db_connection() -> None:
    """
    Reset the database connection pool.

    This is primarily useful for testing scenarios where you need to
    ensure a fresh database connection. In production, this should
    rarely be needed.
    """
    global _engine, _session_factory

    if _engine is not None:
        # Note: In a real application, you'd want to properly dispose
        # of the engine, but for SQLite this is less critical
        _engine = None
    _session_factory = None
