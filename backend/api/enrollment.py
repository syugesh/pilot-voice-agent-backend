"""
Enrollment API — mic recording + file upload → embedding → identity
Features: start, submit audio (mic or file), finalize, list, delete
"""
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Header, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from db.engine import get_db
from db.models import VoiceEnrollment
from core.security import decode_token
import asyncio, logging

router = APIRouter()
logger = logging.getLogger("pilot.enrollment")


class StartReq(BaseModel):
    name: str
    role: str


def _current_user_id(authorization: str | None) -> int | None:
    """Decode the bearer token to find which user is enrolling. Without this,
    enrollments are orphaned (user_id=None) and login can never see them as enrolled."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        payload = decode_token(authorization.removeprefix("Bearer ").strip())
        return int(payload["sub"])
    except Exception:
        return None


@router.get("")
async def list_speakers(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(VoiceEnrollment).where(VoiceEnrollment.status == "ready")
    )).scalars().all()
    return [{"id": r.id, "name": r.speaker_name, "role": r.role,
             "voice_id": f"#{r.id:06X}"} for r in rows]


@router.post("/start")
async def start(req: StartReq, db: AsyncSession = Depends(get_db),
                 authorization: str | None = Header(None)):
    user_id = _current_user_id(authorization)
    if user_id is None:
        raise HTTPException(401, "Login required before voice enrollment")

    # Re-enrolling: reuse this user's prior row instead of creating a duplicate
    e = (await db.execute(
        select(VoiceEnrollment).where(VoiceEnrollment.user_id == user_id)
    )).scalar_one_or_none()
    if e is None:
        e = VoiceEnrollment(user_id=user_id, speaker_name=req.name, role=req.role)
        db.add(e)
    else:
        e.speaker_name = req.name
        e.role = req.role
        e.status = "pending"
    await db.commit()
    await db.refresh(e)
    return {"speaker_id": e.id, "name": req.name, "role": req.role}


@router.post("/audio")
@limiter.limit("10/minute")
async def submit_audio(
    request: Request,
    speaker_id: str = Form(...),
    audio: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    """Accepts both mic recordings (webm/wav) and uploaded audio files."""
    e = (await db.execute(
        select(VoiceEnrollment).where(VoiceEnrollment.id == int(speaker_id))
    )).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Enrollment not found")

    audio_bytes = await audio.read()
    if len(audio_bytes) < 1000:
        raise HTTPException(400, "Audio too short — record at least 3 seconds")

    from services.enrollment import extract_and_store
    emb_bytes, quality = await extract_and_store(int(speaker_id), audio_bytes)
    e.embedding = emb_bytes   # stored as BLOB in SQLite — no .npy file
    e.npy_path = None          # deprecated
    e.status = "ready"
    await db.commit()
    voice_id = f"#{int(speaker_id):06X}"
    logger.info(f"Enrollment complete: {e.speaker_name} → {voice_id} quality={quality:.2f}")
    return {"status": "ready", "speaker_id": speaker_id,
            "voice_id": voice_id, "quality": round(quality, 3),
            "confidence_pct": int(quality * 100)}


@router.post("/finalize/{speaker_id}")
async def finalize(speaker_id: int, db: AsyncSession = Depends(get_db)):
    e = (await db.execute(
        select(VoiceEnrollment).where(VoiceEnrollment.id == speaker_id)
    )).scalar_one_or_none()
    if not e:
        raise HTTPException(404)
    e.status = "ready"
    await db.commit()
    return {"status": "ready", "voice_id": f"#{speaker_id:06X}"}


@router.delete("/{speaker_id}")
async def delete(speaker_id: int, db: AsyncSession = Depends(get_db)):
    e = (await db.execute(
        select(VoiceEnrollment).where(VoiceEnrollment.id == speaker_id)
    )).scalar_one_or_none()
    if e:
        await db.delete(e)
        await db.commit()
    return {"status": "deleted"}
