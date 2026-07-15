from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.engine import get_db
from backend.db.models import TranscriptLog

router = APIRouter()


@router.get("/{session_id}")
async def get(session_id: str, db: AsyncSession = Depends(get_db)):
    rows = (
        (
            await db.execute(
                select(TranscriptLog)
                .where(TranscriptLog.session_id == session_id)
                .order_by(TranscriptLog.timestamp)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "speaker": r.speaker_id,
            "role": r.role,
            "text": r.text,
            "confidence": r.confidence,
            "timestamp": r.timestamp,
        }
        for r in rows
    ]


import time
from backend.queues.bus import bus, TranscriptSpan

@router.post("/{session_id}")
async def create(session_id: str, data: dict, db: AsyncSession = Depends(get_db)):
    text = data.get("text", "").strip()
    if not text:
        return {"status": "error", "message": "Text cannot be empty"}

    speaker = data.get("speaker", "You")
    role = data.get("role", "user")

    # 1. Persist to database
    log = TranscriptLog(
        session_id=session_id,
        speaker_id=speaker,
        role=role,
        text=text,
        confidence=1.0,
        timestamp=time.time()
    )
    db.add(log)
    await db.commit()

    # 2. Emit WebSocket message to all participants
    span = TranscriptSpan(
        text=text,
        session_id=session_id,
        speaker_id=speaker,
        role=role,
        confidence=1.0,
        timestamp=time.time()
    )
    await bus.emit(
        "transcript",
        {
            "speaker": span.speaker_id,
            "role": span.role,
            "text": span.text,
            "confidence": span.confidence,
            "timestamp": span.timestamp,
        },
        session_id=session_id,
    )

    # 3. Queue the transcript span to trigger FrontLLM agent orchestrator
    await bus.transcript_q.put(span)

    return {"status": "ok"}
