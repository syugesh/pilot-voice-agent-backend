"""
Snapshot persistence — serialize/deserialize session state to SQLite.
Enables WebSocket reconnection without losing context.
"""
import json
import logging
from core.session_state import get_state

logger = logging.getLogger("pilot.persistence")


async def save_snapshot(session_id: str):
    state = get_state(session_id)
    snapshot = {
        "session_id": session_id,
        "ring_buffer": list(state.ring_buffer),
        "current_speaker": state.current_speaker,
        "current_role": state.current_role,
    }
    from db.engine import AsyncSessionLocal
    from db.models import Session
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Session).where(Session.session_id == session_id))
        s = result.scalar_one_or_none()
        if s:
            s.snapshot = json.dumps(snapshot)
            await db.commit()


async def load_snapshot(session_id: str) -> dict | None:
    from db.engine import AsyncSessionLocal
    from db.models import Session
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Session).where(Session.session_id == session_id))
        s = result.scalar_one_or_none()
        if s and s.snapshot:
            return json.loads(s.snapshot)
    return None
