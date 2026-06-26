"""
WebSocket connection pool — tracks all live /ws/events connections per session.
"""
from fastapi import WebSocket
import json
import logging

logger = logging.getLogger("pilot.ws_manager")

_connections: dict[str, list[WebSocket]] = {}


def register(session_id: str, ws: WebSocket):
    _connections.setdefault(session_id, []).append(ws)


def unregister(session_id: str, ws: WebSocket):
    conns = _connections.get(session_id, [])
    if ws in conns:
        conns.remove(ws)


async def broadcast(session_id: str, event: dict):
    dead = []
    for ws in _connections.get(session_id, []):
        try:
            await ws.send_text(json.dumps(event))
        except Exception:
            dead.append(ws)
    for ws in dead:
        unregister(session_id, ws)


async def broadcast_all(event: dict):
    for sid in list(_connections.keys()):
        await broadcast(sid, event)
