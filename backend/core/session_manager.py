"""
Session registry + state machine.
Pipeline workers call transition() to move through states.
"""
import asyncio, logging
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

logger = logging.getLogger("pilot.session_mgr")


class SessionState(str, Enum):
    IDLE        = "IDLE"
    LISTENING   = "LISTENING"
    PROCESSING  = "PROCESSING"   # VAD fired, ASR running
    DELEGATING  = "DELEGATING"   # Tool dispatched, bg agent running
    SPEAKING    = "SPEAKING"     # TTS playing on client
    INTERRUPTED = "INTERRUPTED"
    ENDED       = "ENDED"


@dataclass
class ActiveSession:
    session_id: str
    user_id:    int
    usecase:    str
    state:      SessionState = SessionState.IDLE
    active_job_ids: list = field(default_factory=list)


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, ActiveSession] = {}

    def register(self, session_id: str, user_id: int, usecase: str) -> ActiveSession:
        s = ActiveSession(session_id=session_id, user_id=user_id, usecase=usecase,
                          state=SessionState.LISTENING)
        self._sessions[session_id] = s
        logger.debug(f"Session registered: {session_id[:8]}…")
        return s

    def get(self, session_id: str) -> Optional[ActiveSession]:
        return self._sessions.get(session_id)

    async def transition(self, session_id: str, new_state: SessionState):
        """Change state and broadcast to browser via event_q."""
        s = self._sessions.get(session_id)
        if not s:
            return
        old = s.state
        s.state = new_state
        logger.debug(f"Session {session_id[:8]}: {old.value} → {new_state.value}")
        # Push state change to browser
        from queues.bus import bus
        await bus.emit_event("session_state", {
            "state": new_state.value,
            "session_id": session_id
        }, session_id)

    def remove(self, session_id: str):
        self._sessions.pop(session_id, None)


session_manager = SessionManager()
