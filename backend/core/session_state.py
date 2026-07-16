"""
Per-session pipeline state — ring buffer, speaker context, TTS flag.
Persisted to SQLite on demand for reconnect support (Feature 2).
"""

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("pilot.session_state")


@dataclass
class SessionPipelineState:
    session_id: str
    # sliding wondow of the most recent conversation transcript lines, providing immediate context of what was said.
    ring_buffer: deque = field(default_factory=lambda: deque(maxlen=50))

    current_speaker: Optional[str] = None
    current_role: Optional[str] = None
    barge_in: bool = False
    tts_playing: bool = False
    tts_start_time: float = 0.0
    # Tracks exactly when TTS playback started to prevent early microphone echo barge-ins
    active_tts_task: Optional[asyncio.Task] = None
    # Use case determines the assistant's goal (e.g., general chat, coding, or customer support) - this is used to customize the LLM's behavior.
    usecase: str = "general"
    # Gate ambient pipeline with a voice wake-word ('hey pilot' / 'stop')
    ambient_listening_active: bool = False

    # Live manually typed context from frontend to override voice queries
    # Flights context
    typed_origin: str = ""
    typed_destination: str = ""
    typed_date: str = ""

    # Hotels context
    typed_hotel_origin: str = ""
    typed_hotel_date: str = ""
    # Real coordinates from the browser's Geolocation API (the "Near me"
    # button) — synced here so a voice-triggered "search hotels near my
    # location" can use them too, not just a manual click of Search Hotels.
    typed_hotel_lat: float | None = None
    typed_hotel_lng: float | None = None

    # Trains context
    typed_train_origin: str = ""
    typed_train_destination: str = ""
    typed_train_date: str = ""

    # Live draft email state for real-time background sending
    pending_email_draft: str = ""
    pending_email_subject: str = ""
    pending_email_recipient_email: str = ""
    pending_email_recipient_name: str = ""
    pending_email_cc_bcc: str = ""
    session_participants: dict = field(default_factory=dict)

    # Last voice-driven slide edit/add — lets ppt_last_action answer "what did
    # you just change?" without re-reading the whole deck.
    last_ppt_action: Optional[dict] = None

    # Set by ppt_add_slide when it asked "what should the new slide be
    # about?" and is waiting for the answer on the next turn — read by
    # services/front_llm.py's classify() to route that follow-up straight
    # back to ppt_add_slide instead of letting it get independently
    # classified. Transient (not persisted in to_snapshot/from_snapshot);
    # a clarification question doesn't need to survive a session restore.
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

    # Appends a new transcript line to the rolling conversation ring_buffer.

    def add_span(self, span: dict):
        self.ring_buffer.append(span)

    # Extracts the last n conversation lines to send to the LLM as context for generating intelligent replies.
    def get_context(self, n: int = 10) -> list[dict]:
        return list(self.ring_buffer)[-n:]

    # Converts critical session state values (history, speaker name, active usecase, etc.) into a JSON string.
    def to_snapshot(self) -> str:
        return json.dumps(
            {
                "ring_buffer": list(self.ring_buffer),
                "current_speaker": self.current_speaker,
                "current_role": self.current_role,
                "usecase": self.usecase,
                "ambient_listening_active": self.ambient_listening_active,
                "last_ppt_action": self.last_ppt_action,
            }
        )

    # Parses a JSON string to rebuild a complete SessionPipelineState object, restoring memory instantly.
    @classmethod
    def from_snapshot(cls, session_id: str, snapshot: str) -> "SessionPipelineState":
        data = json.loads(snapshot)
        s = cls(session_id=session_id)
        s.ring_buffer = deque(data.get("ring_buffer", []), maxlen=50)
        s.current_speaker = data.get("current_speaker")
        s.current_role = data.get("current_role")
        s.usecase = data.get("usecase", "customercare")
        s.ambient_listening_active = data.get("ambient_listening_active", False)
        s.last_ppt_action = data.get("last_ppt_action")
        return s


_states: dict[str, SessionPipelineState] = {}


def get_state(session_id: str) -> SessionPipelineState:
    if session_id not in _states:
        _states[session_id] = SessionPipelineState(session_id=session_id)
    return _states[session_id]


def clear_state(session_id: str):
    _states.pop(session_id, None)


# Writes the JSON snapshot of the session state into the snapshot column of the SQLite database.
async def persist_state(session_id: str):
    """Save ring buffer to SQLite so reconnects restore context."""
    state = _states.get(session_id)
    if not state:
        return
    try:
        from sqlalchemy import select

        from backend.db.engine import AsyncSessionLocal
        from backend.db.models import Session

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Session).where(Session.session_id == session_id))
            s = result.scalar_one_or_none()
            if s:
                s.snapshot = state.to_snapshot()
                await db.commit()
    except Exception as e:
        logger.error(f"persist_state error: {e}")


# Fetches the snapshot from SQLite upon a user reconnecting, and initializes the in-memory _states dictionary with it.
async def restore_state(session_id: str) -> bool:
    """Restore ring buffer from SQLite on WS reconnect."""
    try:
        from sqlalchemy import select

        from backend.db.engine import AsyncSessionLocal
        from backend.db.models import Session

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
