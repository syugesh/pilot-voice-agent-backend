from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.engine import get_db
from db.models import TranscriptLog

router = APIRouter()


@router.get("/{session_id}")
async def get(session_id: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(TranscriptLog).where(TranscriptLog.session_id == session_id)
        .order_by(TranscriptLog.timestamp)
    )).scalars().all()
    return [{"speaker": r.speaker_id, "role": r.role, "text": r.text,
             "confidence": r.confidence, "timestamp": r.timestamp} for r in rows]
