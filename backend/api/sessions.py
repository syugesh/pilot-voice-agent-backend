from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel
from db.engine import get_db
from db.models import Session as PilotSession, TranscriptLog, AuditLog
import uuid

router = APIRouter()


class CreateSessionReq(BaseModel):
    usecase: str  # ppt | customercare | general


def _mask(session_id: str) -> str:
    return session_id[:8] if session_id else ""


def _role_from_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        from core.security import decode_token
        payload = decode_token(authorization.split(" ", 1)[1])
        return payload.get("role")
    except Exception:
        return None


@router.post("")
async def create(req: CreateSessionReq, db: AsyncSession = Depends(get_db),
                 authorization: str | None = Header(None)):
    sid = str(uuid.uuid4())
    db.add(PilotSession(session_id=sid, usecase=req.usecase, user_id=None))
    await db.commit()
    from core.session_manager import session_manager
    from core.session_state import get_state
    session_manager.register(sid, 0, req.usecase)
    # Store authenticated user's role so voice ID fallback uses correct permissions
    role = _role_from_token(authorization)
    if role:
        get_state(sid).fallback_role = role
    return {"session_id": sid, "usecase": req.usecase, "state": "IDLE"}


@router.get("/list")
async def list_sessions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PilotSession).order_by(desc(PilotSession.id)).limit(20)
    )
    rows = result.scalars().all()
    return {"sessions": [
        {
            "session_id":  s.session_id,
            "display_id":  _mask(s.session_id),
            "usecase":     s.usecase,
            "state":       s.state,
            "created_at":  str(s.created_at),
        }
        for s in rows
    ]}


@router.get("/{session_id}/history")
async def session_history(session_id: str, db: AsyncSession = Depends(get_db)):
    """Return session info + transcript + audit actions for the history popup."""
    s = (await db.execute(
        select(PilotSession).where(PilotSession.session_id == session_id)
    )).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Session not found")

    transcripts = (await db.execute(
        select(TranscriptLog)
        .where(TranscriptLog.session_id == session_id)
        .order_by(TranscriptLog.timestamp)
    )).scalars().all()

    actions = (await db.execute(
        select(AuditLog)
        .where(AuditLog.session_id == session_id)
        .order_by(AuditLog.timestamp)
    )).scalars().all()

    return {
        "session": {
            "session_id":  s.session_id,
            "display_id":  _mask(s.session_id),
            "usecase":     s.usecase,
            "state":       s.state,
            "created_at":  str(s.created_at),
            "ended_at":    str(s.ended_at) if s.ended_at else None,
        },
        "transcripts": [
            {"speaker": t.speaker_id, "role": t.role,
             "text": t.text, "timestamp": t.timestamp}
            for t in transcripts
        ],
        "actions": [
            {"tool": a.tool, "decision": a.decision,
             "latency_ms": a.latency_ms, "timestamp": a.timestamp}
            for a in actions
        ],
    }


@router.get("/{session_id}")
async def get(session_id: str, db: AsyncSession = Depends(get_db)):
    s = (await db.execute(select(PilotSession).where(PilotSession.session_id == session_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Session not found")
    return {"session_id": s.session_id, "usecase": s.usecase, "state": s.state}


@router.delete("/{session_id}")
async def end(session_id: str, db: AsyncSession = Depends(get_db)):
    s = (await db.execute(select(PilotSession).where(PilotSession.session_id == session_id))).scalar_one_or_none()
    if s:
        from datetime import datetime
        s.ended_at = datetime.utcnow()
        s.state = "ENDED"
        await db.commit()
    from core.session_manager import session_manager
    session_manager.remove(session_id)
    return {"status": "ended"}
