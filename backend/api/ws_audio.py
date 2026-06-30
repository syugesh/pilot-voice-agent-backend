"""WebSocket /ws/audio — receives raw PCM from browser."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from queues.bus import bus, RawAudioChunk
import time, logging

router = APIRouter()
logger = logging.getLogger("pilot.ws.audio")


@router.websocket("/ws/audio/{session_id}")
async def ws_audio(websocket: WebSocket, session_id: str):
    await websocket.accept()

    # Register session as LISTENING
    from core.session_manager import session_manager, SessionState
    if not session_manager.get(session_id):
        session_manager.register(session_id, 0, "unknown")
    await session_manager.transition(session_id, SessionState.LISTENING)
    logger.info(f"Audio WS connected: {session_id[:8]}")

    try:
        while True:
            data = await websocket.receive_bytes()
            chunk = RawAudioChunk(pcm=data, session_id=session_id, timestamp=time.time())
            try:
                bus.raw_audio_q.put_nowait(chunk)
            except Exception:
                pass  # backpressure — drop
    except WebSocketDisconnect:
        await session_manager.transition(session_id, SessionState.ENDED)
        logger.info(f"Audio WS disconnected: {session_id[:8]}")
