"""WebSocket /ws/audio — receives raw PCM from browser."""
'''It detects when you speak over PILOT while PILOT is talking and immediately cuts off its voice playback.'''
import logging

import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.queues.bus import RawAudioChunk, bus

router = APIRouter()
logger = logging.getLogger("pilot.ws.audio")


# implemented a primary real time ingestion endpoint for raw audio  binary stream arriving from the browsermicrophone client. 

@router.websocket("/ws/audio/{session_id}")
async def ws_audio(websocket: WebSocket, session_id: str):
    # Verify token before registering participant and accepting PCM audio frames
    token = websocket.query_params.get("token")
    if not token:
        logger.warning(f"Rejecting WS audio connection to session {session_id}: Missing token")
        await websocket.accept()
        await websocket.close(code=1008)
        return

    try:
        from backend.core.security import decode_token
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except Exception as e:
        logger.warning(f"Rejecting WS audio connection to session {session_id}: Invalid token ({e})")
        await websocket.accept()
        await websocket.close(code=1008)
        return

    # Database lookup to securely resolve user identity
    email = None
    name = None
    try:
        from sqlalchemy import select
        from backend.db.engine import AsyncSessionLocal
        from backend.db.models import User
        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if user:
                email = user.email
                name = user.name
    except Exception as e:
        logger.error(f"WS audio database lookup error: {e}")

    # Fallback to query params or token payload if db lookup did not yield values
    if not email:
        email = websocket.query_params.get("email") or payload.get("email")
    if not name:
        name = websocket.query_params.get("name")

    # handshake and participant registration. 
    await websocket.accept()

    if email and name:
        from backend.core.session_state import get_state
        # if got it start the session when click on the mic. 
        state = get_state(session_id)
        if not hasattr(state, "session_participants"):
            state.session_participants = {}
        state.session_participants[email] = name
        logger.info(f"[{session_id[:6]}] Registered WS audio participant: {name} ({email})")

    # Register session as LISTENING
    from backend.core.session_manager import SessionState, session_manager
    from backend.core.session_state import get_state


    # if still mananger has not yet cached this session , backend performs a async database query to load the session's ,metadata. 
    if not session_manager.get(session_id):
        from sqlalchemy import select

        from backend.db.engine import AsyncSessionLocal
        from backend.db.models import Session as PilotSession

        db_user_id = 0
        usecase = "unknown"
        try:
            async with AsyncSessionLocal() as db:
                s = (
                    await db.execute(select(PilotSession).where(PilotSession.session_id == session_id))
                ).scalar_one_or_none()
                if s:
                    db_user_id = s.user_id or 0
                    usecase = s.usecase
        except Exception as e:
            logger.error(f"Failed to load session user from DB: {e}")
        # Talkinia meeting sessions never go through POST /sessions (see
        # TALKINIA_STREAM/components/MeetingRoom.tsx) — they connect
        # straight to this WS with a synthetic session_id shaped
        # "meeting_{meetingId}", so there's never a matching PilotSession
        # row and usecase stays "unknown" above. That id shape is a
        # reliable signal on its own to route them into "meeting" usecase
        # instead, which asr_worker.py uses to stay passive (listen +
        # transcribe only, respond only to an explicit wake-word command)
        # rather than treating ambient conversation as commands.
        if usecase == "unknown" and session_id.startswith("meeting_"):
            usecase = "meeting"
        session_manager.register(session_id, db_user_id, usecase)
        # ActiveSession (session_manager) and SessionPipelineState
        # (session_state, what the ASR pipeline actually reads per-turn)
        # are two separate objects — sync the resolved usecase onto both,
        # or state.usecase silently stays at its dataclass default
        # ("general") regardless of what this session's real usecase is.
        get_state(session_id).usecase = usecase

    # once the manager got the session metadata , the session is registered in the state manager and transitioned to listening . 
    await session_manager.transition(session_id, SessionState.LISTENING)
    logger.info(f"Audio WS connected: {session_id[:8]}")

    try:
        # continuous listening . 
        while True:
            # blocking async on incoming binary data. 
            data = await websocket.receive_bytes()

            chunk = RawAudioChunk(pcm=data, session_id=session_id, timestamp=time.time())

            # RMS-based barge-in (stop TTS the instant real speech is heard,
            # not just on a recognized "stop" phrase) lives in
            # pipeline/vad/silero_vad.py's SileroVADWorker._maybe_barge_in —
            # guarded by a post-TTS-start grace window there specifically to
            # avoid the ambient/mic-echo self-interruption problem this
            # comment used to warn about. Explicit stop words ("stop",
            # "cancel") are a separate, more drastic path that also cancels
            # the in-flight background job — see ws_events.py's
            # stop_command handler.

            try:
                bus.raw_audio_q.put_nowait(chunk)
            except Exception:
                pass  # backpressure — drop
    except WebSocketDisconnect:
        await session_manager.transition(session_id, SessionState.ENDED)
        logger.info(f"Audio WS disconnected: {session_id[:8]}")
