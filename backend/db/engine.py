"""Async SQLite engine + session factory. FSE-A owns this."""

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.core.config import settings

os.makedirs("data", exist_ok=True)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    from backend.db import models  # noqa — registers all ORM classes

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def migrate_status_col(connection):
            # Using raw SQLite connection to safely add the column if missing
            dbapi_conn = connection.connection
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA table_info(users);")
            cols = [c[1] for c in cursor.fetchall()]
            if "status" not in cols:
                cursor.execute("ALTER TABLE users ADD COLUMN status VARCHAR DEFAULT 'offline';")
                dbapi_conn.commit()

        await conn.run_sync(migrate_status_col)

        def migrate_tickets_cols(connection):
            # A `tickets` table pre-dating the Ticket model (created outside
            # this app, missing the Customer Resolution columns) sits in some
            # dev DBs — create_all() skips tables that already exist, so it
            # never picks up priority/escalated/escalation_target on its own.
            dbapi_conn = connection.connection
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA table_info(tickets);")
            cols = [c[1] for c in cursor.fetchall()]
            if not cols:
                return  # table doesn't exist yet — create_all() above already made the current shape
            if "priority" not in cols:
                cursor.execute("ALTER TABLE tickets ADD COLUMN priority VARCHAR DEFAULT 'normal';")
            if "escalated" not in cols:
                cursor.execute("ALTER TABLE tickets ADD COLUMN escalated BOOLEAN DEFAULT 0;")
            if "escalation_target" not in cols:
                cursor.execute("ALTER TABLE tickets ADD COLUMN escalation_target VARCHAR;")
            dbapi_conn.commit()

        await conn.run_sync(migrate_tickets_cols)

        def setup_cascade_triggers(connection):
            dbapi_conn = connection.connection
            cursor = dbapi_conn.cursor()

            # Trigger to delete voice enrollments when user is deleted
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS cascade_delete_voice_enrollments
                AFTER DELETE ON users
                FOR EACH ROW
                BEGIN
                    DELETE FROM voice_enrollments WHERE user_id = OLD.id;
                END;
            """)

            # Trigger to delete group members when user is deleted
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS cascade_delete_group_members
                AFTER DELETE ON users
                FOR EACH ROW
                BEGIN
                    DELETE FROM group_members WHERE user_id = OLD.id;
                END;
            """)

            # Trigger to delete groups created by the deleted user
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS cascade_delete_groups
                AFTER DELETE ON users
                FOR EACH ROW
                BEGIN
                    DELETE FROM groups WHERE created_by = OLD.id;
                END;
            """)

            # Trigger to delete sessions linked to the deleted user
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS cascade_delete_sessions
                AFTER DELETE ON users
                FOR EACH ROW
                BEGIN
                    DELETE FROM sessions WHERE user_id = OLD.id;
                END;
            """)

            # Trigger to delete group members when a group is deleted
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS cascade_delete_group_members_on_group_delete
                AFTER DELETE ON groups
                FOR EACH ROW
                BEGIN
                    DELETE FROM group_members WHERE group_id = OLD.id;
                END;
            """)
            dbapi_conn.commit()

        await conn.run_sync(setup_cascade_triggers)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
