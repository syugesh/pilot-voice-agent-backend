"""
Per-session pipeline state — ring buffer, speaker context, TTS flag.
Persisted to SQLite on demand for reconnect support (Feature 2).
"""
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Any
import json, logging

logger = logging.getLogger("pilot.session_state")

@dataclass
class SessionPipelineState:
    session_id: str
    ring_buffer: deque = field(default_factory=lambda: deque(maxlen=50))
    current_speaker: Optional[str] = None
    current_role: Optional[str] = None
    barge_in: bool = False
    tts_playing: bool = False
    usecase: str = "customercare"
    fallback_role: Optional[str] = None   # JWT-authenticated user role, used when voice ID fails
    fallback_name: Optional[str] = None   # JWT-authenticated user's real name, used when voice ID fails
    # UI-provided overrides for travel search (set by frontend form, if present)
    typed_origin: Optional[str] = None
    typed_destination: Optional[str] = None
    typed_date: Optional[str] = None
    last_ppt_action: Optional[dict] = None
    active_tts_task: Optional[Any] = field(default=None, repr=False)
    tts_start_time: float = 0.0
    # Set by ppt_add_slide when the user's instruction had no real topic
    # ("add a slide" with nothing else) — the tool asks what it should be
    # about instead of inventing content, and the NEXT utterance is routed
    # straight back to ppt_add_slide as the answer rather than being
    # independently (mis)classified. {"insert_after": int|None}. Not
    # persisted to the DB snapshot — a same-session clarification shouldn't
    # survive a reconnect and silently fire on an unrelated later utterance.
    pending_add_slide: Optional[dict] = None
    # Rolling per-turn customer sentiment for the CSR resolution dashboard —
    # the resolution engine reads this to detect *sustained* high frustration
    # (a single spike isn't an escalation signal; a trend is). Capped small;
    # not persisted to the DB snapshot (live-call state, not history).
    sentiment_history: list = field(default_factory=list)

    def add_sentiment(self, entry: dict):
        self.sentiment_history.append(entry)
        if len(self.sentiment_history) > 20:
            self.sentiment_history = self.sentiment_history[-20:]

    def add_span(self, span: dict):
        self.ring_buffer.append(span)

    def get_context(self, n: int = 10) -> list[dict]:
        return list(self.ring_buffer)[-n:]

    def to_snapshot(self) -> str:
        return json.dumps({
            "ring_buffer": list(self.ring_buffer),
            "current_speaker": self.current_speaker,
            "current_role": self.current_role,
            "usecase": self.usecase,
            "last_ppt_action": self.last_ppt_action,
        })

    @classmethod
    def from_snapshot(cls, session_id: str, snapshot: str) -> "SessionPipelineState":
        data = json.loads(snapshot)
        s = cls(session_id=session_id)
        s.ring_buffer = deque(data.get("ring_buffer", []), maxlen=50)
        s.current_speaker = data.get("current_speaker")
        s.current_role = data.get("current_role")
        s.usecase = data.get("usecase", "customercare")
        s.last_ppt_action = data.get("last_ppt_action")
        return s


_states: dict[str, SessionPipelineState] = {}


def get_state(session_id: str) -> SessionPipelineState:
    if session_id not in _states:
        # Inherit usecase from session_manager so routing filters work correctly
        try:
            from core.session_manager import session_manager
            sess = session_manager.get(session_id)
            usecase = sess.usecase if sess else "general"
        except Exception:
            usecase = "general"
        _states[session_id] = SessionPipelineState(session_id=session_id, usecase=usecase)
    return _states[session_id]


def clear_state(session_id: str):
    _states.pop(session_id, None)


async def persist_state(session_id: str):
    """Save ring buffer to SQLite so reconnects restore context."""
    state = _states.get(session_id)
    if not state:
        return
    try:
        from db.engine import AsyncSessionLocal
        from db.models import Session
        from sqlalchemy import select
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Session).where(Session.session_id == session_id))
            s = result.scalar_one_or_none()
            if s:
                s.snapshot = state.to_snapshot()
                await db.commit()
    except Exception as e:
        logger.error(f"persist_state error: {e}")


async def restore_state(session_id: str) -> bool:
    """Restore ring buffer from SQLite on WS reconnect."""
    try:
        from db.engine import AsyncSessionLocal
        from db.models import Session
        from sqlalchemy import select
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Session).where(Session.session_id == session_id))
            s = result.scalar_one_or_none()
            if s and s.snapshot:
                _states[session_id] = SessionPipelineState.from_snapshot(session_id, s.snapshot)
                logger.info(f"State restored for {session_id[:8]}")
                return True
    except Exception as e:
        logger.error(f"restore_state error: {e}")
    return False
