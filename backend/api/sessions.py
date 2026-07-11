from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel
from db.engine import get_db
from db.models import Session as PilotSession, TranscriptLog, AuditLog, User
import uuid

router = APIRouter()

# Summaries are only cached once a session has ENDED (immutable at that point) —
# an in-progress session's summary would go stale, so those are always
# regenerated fresh instead of cached.
_summary_cache: dict[str, str] = {}


class CreateSessionReq(BaseModel):
    usecase: str  # ppt | customercare | general


def _mask(session_id: str) -> str:
    return session_id[:8] if session_id else ""


def _claims_from_token(authorization: str | None) -> dict | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        from core.security import decode_token
        return decode_token(authorization.split(" ", 1)[1])
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
    # Store authenticated user's role + name so voice ID fallback uses correct
    # permissions and the transcript shows the real name instead of "You"
    claims = _claims_from_token(authorization)
    if claims:
        state = get_state(sid)
        if claims.get("role"):
            state.fallback_role = claims["role"]
        if claims.get("email"):
            user = (await db.execute(
                select(User).where(User.email == claims["email"])
            )).scalar_one_or_none()
            if user:
                state.fallback_name = user.name
    return {"session_id": sid, "usecase": req.usecase, "state": "IDLE"}


_USECASE_LABELS = {"ppt": "Presentation", "customercare": "Customer Care", "general": "General"}


def _make_title(usecase: str, first_line: str | None) -> str:
    """A random session_id/UUID means nothing to a user browsing their history —
    title the session by what was actually first said in it instead, the same
    way chat apps title conversations by their opening message."""
    label = _USECASE_LABELS.get(usecase, usecase.title() if usecase else "Session")
    if not first_line:
        return f"New {label} Session"
    text = first_line.strip()
    return (text[:57] + "…") if len(text) > 57 else text


@router.get("/list")
async def list_sessions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PilotSession).order_by(desc(PilotSession.id)).limit(20)
    )
    rows = result.scalars().all()

    sessions = []
    for s in rows:
        first = (await db.execute(
            select(TranscriptLog.text)
            .where(TranscriptLog.session_id == s.session_id, TranscriptLog.speaker_id != "PILOT")
            .order_by(TranscriptLog.timestamp)
            .limit(1)
        )).scalar_one_or_none()
        sessions.append({
            "session_id":  s.session_id,
            "display_id":  _mask(s.session_id),
            "title":       _make_title(s.usecase, first),
            "usecase":     s.usecase,
            "state":       s.state,
            "created_at":  str(s.created_at),
        })
    return {"sessions": sessions}


@router.get("/stats")
async def session_stats(db: AsyncSession = Depends(get_db)):
    """Aggregate dashboard tiles — sessions/transcripts/tools today vs
    yesterday, and today's average tool latency. Real SQL aggregation over
    Session/TranscriptLog/AuditLog, not client-derived estimates, since the
    dashboard only ever sees the last 20 sessions via /list."""
    import datetime
    from sqlalchemy import func

    now = datetime.datetime.utcnow()
    today_start = datetime.datetime(now.year, now.month, now.day)
    yesterday_start = today_start - datetime.timedelta(days=1)

    async def _count_since(model, ts_col, since, until=None):
        q = select(func.count()).select_from(model).where(ts_col >= since)
        if until is not None:
            q = q.where(ts_col < until)
        return (await db.execute(q)).scalar() or 0

    def _delta_pct(today: int, yesterday: int) -> float | None:
        if yesterday == 0:
            return None  # avoid a misleading "+inf%" — frontend shows "—" instead
        return round((today - yesterday) / yesterday * 100, 1)

    sessions_today     = await _count_since(PilotSession, PilotSession.created_at, today_start)
    sessions_yesterday = await _count_since(PilotSession, PilotSession.created_at, yesterday_start, today_start)

    transcripts_today     = await _count_since(TranscriptLog, TranscriptLog.created_at, today_start)
    transcripts_yesterday = await _count_since(TranscriptLog, TranscriptLog.created_at, yesterday_start, today_start)

    tools_today = (await db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.action == "tool_call", AuditLog.timestamp >= today_start)
    )).scalar() or 0
    tools_yesterday = (await db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.action == "tool_call", AuditLog.timestamp >= yesterday_start, AuditLog.timestamp < today_start)
    )).scalar() or 0

    avg_latency_today = (await db.execute(
        select(func.avg(AuditLog.latency_ms)).select_from(AuditLog)
        .where(AuditLog.action == "tool_call", AuditLog.timestamp >= today_start, AuditLog.latency_ms.is_not(None))
    )).scalar()
    avg_latency_yesterday = (await db.execute(
        select(func.avg(AuditLog.latency_ms)).select_from(AuditLog)
        .where(AuditLog.action == "tool_call", AuditLog.timestamp >= yesterday_start, AuditLog.timestamp < today_start, AuditLog.latency_ms.is_not(None))
    )).scalar()

    return {
        "sessions_today": sessions_today, "sessions_delta_pct": _delta_pct(sessions_today, sessions_yesterday),
        "transcripts_today": transcripts_today, "transcripts_delta_pct": _delta_pct(transcripts_today, transcripts_yesterday),
        "tools_today": tools_today, "tools_delta_pct": _delta_pct(tools_today, tools_yesterday),
        "avg_latency_ms": round(avg_latency_today) if avg_latency_today is not None else None,
        "avg_latency_ms_yesterday": round(avg_latency_yesterday) if avg_latency_yesterday is not None else None,
    }


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

    transcript_dicts = [
        {"speaker": t.speaker_id, "role": t.role, "text": t.text, "timestamp": t.timestamp}
        for t in transcripts
    ]
    action_dicts = [
        {"tool": a.tool, "decision": a.decision, "latency_ms": a.latency_ms, "timestamp": a.timestamp}
        for a in actions
    ]

    if s.state == "ENDED" and session_id in _summary_cache:
        summary = _summary_cache[session_id]
    else:
        from services.session_summary import summarize_session
        summary = await summarize_session(transcript_dicts, action_dicts, s.usecase)
        if s.state == "ENDED":
            _summary_cache[session_id] = summary

    return {
        "session": {
            "session_id":  s.session_id,
            "display_id":  _mask(s.session_id),
            "usecase":     s.usecase,
            "state":       s.state,
            "created_at":  str(s.created_at),
            "ended_at":    str(s.ended_at) if s.ended_at else None,
        },
        "summary":     summary,
        "transcripts": transcript_dicts,
        "actions":     action_dicts,
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
