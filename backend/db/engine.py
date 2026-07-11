"""Async SQLite engine + session factory. FSE-A owns this."""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from core.config import settings
from pathlib import Path
import os

# Anchor relative sqlite paths to this file's directory (backend/), not the
# process's cwd. DATABASE_URL is normally a relative path (./data/pilot.db),
# and cwd depends on however the server happens to be launched — a different
# terminal/IDE run config/script starting uvicorn from a different directory
# silently created a brand-new empty DB file there instead of erroring, which
# is why the "real" data/pilot.db looked empty on some runs.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_SQLITE_PREFIX = "sqlite+aiosqlite:///"


def _resolve_database_url(url: str) -> str:
    if url.startswith(_SQLITE_PREFIX):
        raw_path = url[len(_SQLITE_PREFIX):]
        if raw_path and not raw_path.startswith("/"):
            return _SQLITE_PREFIX + str((_BACKEND_DIR / raw_path).resolve())
    return url


DATABASE_URL = _resolve_database_url(settings.DATABASE_URL)

os.makedirs(_BACKEND_DIR / "data", exist_ok=True)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(DATABASE_URL, echo=False)
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
        "ALTER TABLE tickets ADD COLUMN priority TEXT DEFAULT 'normal'",
        "ALTER TABLE tickets ADD COLUMN escalated BOOLEAN DEFAULT 0",
        "ALTER TABLE tickets ADD COLUMN escalation_target TEXT",
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
