"""
WebSocket /ws/events — server-push pipeline state to browser.
Restores session state on reconnect (Feature 2).
"""
import asyncio, json, logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from core.ws_manager import register, unregister, broadcast
from queues.bus import bus

router = APIRouter()
logger = logging.getLogger("pilot.ws.events")

_session_queues: dict[str, list[asyncio.Queue]] = {}
_route_task: asyncio.Task | None = None


@router.websocket("/ws/events/{session_id}")
async def ws_events(websocket: WebSocket, session_id: str):
    global _route_task
    await websocket.accept()
    register(session_id, websocket)
    logger.info(f"Events WS connected: {session_id[:8]}")

    # Restore session state on reconnect
    from core.session_state import restore_state
    await restore_state(session_id)

    # Start global event router once
    if _route_task is None or _route_task.done():
        _route_task = asyncio.create_task(route_events(), name="EventRouter")
        logger.info("EventRouter started")

    local_q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _session_queues.setdefault(session_id, []).append(local_q)
    drainer = asyncio.create_task(_drain(session_id, local_q))

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=25)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        drainer.cancel()
        qs = _session_queues.get(session_id, [])
        if local_q in qs:
            qs.remove(local_q)
        unregister(session_id, websocket)
        # Persist state and clean up memory when client disconnects
        from core.session_state import persist_state
        from services.front_llm import clear_memory
        await persist_state(session_id)
        clear_memory(session_id)
        logger.info(f"Events WS disconnected: {session_id[:8]}")


async def _drain(session_id: str, local_q: asyncio.Queue):
    try:
        while True:
            event = await local_q.get()
            await broadcast(session_id, {"type": event.type, "payload": event.payload})
    except asyncio.CancelledError:
        pass


async def route_events():
    logger.info("route_events running")
    while True:
        try:
            event = await bus.event_q.get()
            targets = (
                list(_session_queues.get(event.session_id, []))
                if event.session_id != "*"
                else [q for qs in _session_queues.values() for q in qs]
            )
            for q in targets:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass
        except Exception as e:
            logger.error(f"route_events error: {e}")
            await asyncio.sleep(0.1)
