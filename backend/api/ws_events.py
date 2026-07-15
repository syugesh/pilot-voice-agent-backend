"""
WebSocket /ws/events — server-push pipeline state to browser.
Restores session state on reconnect (Feature 2).
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.core.session_state import get_state
from backend.core.ws_manager import broadcast, register, unregister
from backend.queues.bus import bus

router = APIRouter()
logger = logging.getLogger("pilot.ws.events")

_session_queues: dict[str, list[asyncio.Queue]] = {}
_route_task: asyncio.Task | None = None


@router.websocket("/ws/events/{session_id}")
async def ws_events(websocket: WebSocket, session_id: str):
    global _route_task

    token = websocket.query_params.get("token")
    if not token:
        logger.warning(f"Rejecting WS events connection to session {session_id}: Missing token")
        await websocket.accept()
        await websocket.close(code=1008)
        return

    try:
        from backend.core.security import decode_token
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except Exception as e:
        logger.warning(f"Rejecting WS events connection to session {session_id}: Invalid token ({e})")
        await websocket.accept()
        await websocket.close(code=1008)
        return

    # Securely resolve participant details from database
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
                user.status = "online"
                await db.commit()
    except Exception as e:
        logger.error(f"WS events database lookup and status update error: {e}")

    # Fallback to query params or token payload if db lookup did not yield values
    if not email:
        email = websocket.query_params.get("email") or payload.get("email")
    if not name:
        name = websocket.query_params.get("name")

    await websocket.accept()

    if email and name:
        state = get_state(session_id)
        if not hasattr(state, "session_participants"):
            state.session_participants = {}
        state.session_participants[email] = name
        logger.info(f"[{session_id[:6]}] Registered WS event participant: {name} ({email})")

    register(session_id, websocket)
    logger.info(f"Events WS connected: {session_id[:8]}")

    # Restore session state on reconnect
    from backend.core.session_state import restore_state

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
                # Intercept client payload strings to capture manual input updates from frontend dynamically
                payload_str = await asyncio.wait_for(websocket.receive_text(), timeout=25)
                try:
                    payload = json.loads(payload_str)
                    logger.info(f"[{session_id[:6]}] Received WS payload: {payload}")
                    if payload.get("type") == "typed_flight_context":
                        state = get_state(session_id)
                        state.typed_origin = payload.get("origin", "")
                        state.typed_destination = payload.get("destination", "")
                        state.typed_date = payload.get("date", "")
                        logger.info(
                            f"[{session_id[:6]}] Synced manual input context: From={state.typed_origin}, To={state.typed_destination}, Date={state.typed_date}"
                        )
                    elif payload.get("type") == "typed_hotel_context":
                        state = get_state(session_id)
                        state.typed_hotel_origin = payload.get("origin", "")
                        state.typed_hotel_date = payload.get("date", "")
                        state.typed_hotel_lat = payload.get("lat")
                        state.typed_hotel_lng = payload.get("lng")
                        logger.info(
                            f"[{session_id[:6]}] Synced manual hotel context: Origin={state.typed_hotel_origin}, "
                            f"Date={state.typed_hotel_date}, Lat={state.typed_hotel_lat}, Lng={state.typed_hotel_lng}"
                        )
                    elif payload.get("type") == "typed_train_context":
                        state = get_state(session_id)
                        state.typed_train_origin = payload.get("origin", "")
                        state.typed_train_destination = payload.get("destination", "")
                        state.typed_train_date = payload.get("date", "")
                        logger.info(
                            f"[{session_id[:6]}] Synced manual train context: From={state.typed_train_origin}, To={state.typed_train_destination}, Date={state.typed_train_date}"
                        )
                    elif payload.get("type") == "typed_email_context":
                        state = get_state(session_id)
                        state.pending_email_recipient_email = payload.get("to", "")
                        state.pending_email_recipient_name = (
                            payload.get("to", "").split("@")[0].title()
                            if "@" in payload.get("to", "")
                            else payload.get("to", "")
                        )
                        state.pending_email_subject = payload.get("subject", "")
                        state.pending_email_cc_bcc = payload.get("cc_bcc", "")
                        logger.info(
                            f"[{session_id[:6]}] Synced manual email input context: To={state.pending_email_recipient_email}, CcBcc={state.pending_email_cc_bcc}, Subject={state.pending_email_subject}"
                        )
                    elif payload.get("type") == "stop_command":
                        # User said "stop"/"cancel"/etc. — cancel this
                        # session's in-flight background job immediately,
                        # rather than letting it run to completion after
                        # audio playback has already been silenced locally.
                        from backend.core.bg_supervisor import bg_supervisor
                        from backend.core.session_manager import SessionState, session_manager

                        cancelled = bg_supervisor.cancel_for_session(session_id)
                        logger.info(f"[{session_id[:6]}] stop_command received — job cancelled: {cancelled}")
                        await session_manager.transition(session_id, SessionState.LISTENING)
                    elif payload.get("type") == "user_confirm":
                        # UI "Confirm" button clicked — resolve pending policy latch window
                        from backend.tools.policy import PENDING_CONFIRMATIONS

                        pending = PENDING_CONFIRMATIONS.get(session_id)
                        if pending:
                            logger.info(
                                f"[{session_id[:6]}] UI confirm received — resolving latch for tool '{pending['tool']}'"
                            )
                            pending["confirmed"] = True
                            pending["event"].set()
                        else:
                            logger.warning(f"[{session_id[:6]}] user_confirm received but no pending confirmation found")
                    elif payload.get("type") == "user_cancel":
                        # UI "Cancel" button clicked — force-close the latch window as declined
                        from backend.tools.policy import PENDING_CONFIRMATIONS

                        pending = PENDING_CONFIRMATIONS.get(session_id)
                        if pending:
                            logger.info(
                                f"[{session_id[:6]}] UI cancel received — rejecting latch for tool '{pending['tool']}'"
                            )
                            pending["confirmed"] = False
                            pending["event"].set()
                    elif payload.get("type") == "chat_message":
                        from backend.core.ws_manager import broadcast_all

                        await broadcast_all(
                            {
                                "type": "chat_message",
                                "payload": {
                                    "sender_id": payload.get("sender_id"),
                                    "sender_name": payload.get("sender_name"),
                                    "target_id": payload.get("target_id"),
                                    "text": payload.get("text"),
                                    "time": payload.get("time"),
                                    "message_id": payload.get("message_id"),
                                },
                            }
                        )
                except Exception as e:
                    logger.error(f"Error processing WS message: {e}", exc_info=True)
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

        # Set status to offline on disconnect if not logged out / not available
        if user_id is not None:
            try:
                from sqlalchemy import select

                from backend.db.engine import AsyncSessionLocal
                from backend.db.models import User

                async with AsyncSessionLocal() as db:
                    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
                    if user and user.status != "not availabel":
                        user.status = "offline"
                        await db.commit()
            except Exception as e:
                logger.error(f"WS disconnect status update error: {e}")

        # Persist state when client disconnects
        from backend.core.session_state import persist_state

        await persist_state(session_id)
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
