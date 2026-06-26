"""Async SQLite engine + session factory. FSE-A owns this."""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from core.config import settings
import os

os.makedirs("data", exist_ok=True)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    from db import models  # noqa — registers all ORM classes
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def migrate_db():
    """Add new columns to existing tables without dropping data (SQLite-safe)."""
    from sqlalchemy import text
    new_cols = [
        "ALTER TABLE users ADD COLUMN oauth_provider TEXT",
        "ALTER TABLE users ADD COLUMN oauth_id TEXT",
    ]
    async with engine.begin() as conn:
        for stmt in new_cols:
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass  # column already exists


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
