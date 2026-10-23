"""Async engine/session factory. PostgreSQL in compose, SQLite for tests and quick start."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from truthguard.storage.orm import Base


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        kwargs: dict[str, object] = {}
        if url.startswith("postgresql"):
            kwargs.update(pool_size=10, max_overflow=10, pool_pre_ping=True)
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        if self.is_sqlite:

            @event.listens_for(self.engine.sync_engine, "connect")
            def _fk_on(dbapi_conn, _record):  # type: ignore[no-untyped-def]
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    @property
    def is_sqlite(self) -> bool:
        return self.url.startswith("sqlite")

    @property
    def is_postgres(self) -> bool:
        return self.url.startswith("postgresql")

    async def create_all(self) -> None:
        """Create tables directly (tests / SQLite). Production PostgreSQL uses Alembic."""
        async with self.engine.begin() as conn:
            if self.is_postgres:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def ping(self) -> bool:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()
