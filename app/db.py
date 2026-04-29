"""Async SQLModel engine + session factory. init_db() creates tables
idempotently on startup."""

from __future__ import annotations
from collections.abc import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from app.config import settings

# echo=False; flip to True when debugging queries.
engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.jobs_db}",
    echo=False,
    future=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Create all tables. Idempotent - safe to call on every startup."""
    from app.jobs import models

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Use as: `session: AsyncSession = Depends(get_session)`."""
    async with async_session_factory() as session:
        yield session
