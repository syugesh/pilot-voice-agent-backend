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
from db.models import VoiceEnrollment, User
from core.security import decode_token
import asyncio, logging

router = APIRouter()
logger = logging.getLogger("pilot.enrollment")


class StartReq(BaseModel):
    name: str
    role: str


async def _current_user(authorization: str | None, db: AsyncSession) -> User:
    """
    Decode the bearer token and load the authenticated user's own DB row.
    Every enrollment mutation must go through this — role and speaker_name
    are taken from here (the authoritative account record), never from
    client-supplied request fields, so a caller can't self-assign a role
    like "admin" that they don't actually hold on their account.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Login required before voice enrollment")
    try:
        payload = decode_token(authorization.removeprefix("Bearer ").strip())
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(401, "Invalid or expired session")
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(401, "Account no longer exists")
    return user


@router.get("")
async def list_speakers(db: AsyncSession = Depends(get_db),
                         authorization: str | None = Header(None)):
    await _current_user(authorization, db)   # any authenticated user may view the roster
    rows = (await db.execute(
        select(VoiceEnrollment).where(VoiceEnrollment.status == "ready")
    )).scalars().all()
    return [{"id": r.id, "name": r.speaker_name, "role": r.role,
             "voice_id": f"#{r.id:06X}"} for r in rows]


@router.post("/start")
async def start(req: StartReq, db: AsyncSession = Depends(get_db),
                 authorization: str | None = Header(None)):
    user = await _current_user(authorization, db)

    # Re-enrolling: reuse this user's prior row instead of creating a duplicate.
    # speaker_name/role always come from the account record, not the request
    # body — req.name/req.role are accepted for API compatibility but ignored.
    e = (await db.execute(
        select(VoiceEnrollment).where(VoiceEnrollment.user_id == user.id)
    )).scalar_one_or_none()
    if e is None:
        e = VoiceEnrollment(user_id=user.id, speaker_name=user.name, role=user.role)
        db.add(e)
    else:
        e.speaker_name = user.name
        e.role = user.role
        e.status = "pending"
    await db.commit()
    await db.refresh(e)
    return {"speaker_id": e.id, "name": user.name, "role": user.role}


@router.post("/audio")
@limiter.limit("10/minute")
async def submit_audio(
    request: Request,
    speaker_id: str = Form(...),
    audio: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None),
):
    """Accepts both mic recordings (webm/wav) and uploaded audio files."""
    user = await _current_user(authorization, db)

    e = (await db.execute(
        select(VoiceEnrollment).where(VoiceEnrollment.id == int(speaker_id))
    )).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Enrollment not found")
    if e.user_id != user.id:
        # Without this, anyone could POST audio against any other user's
        # speaker_id and silently overwrite their stored voiceprint with
        # their own — a full identity-takeover vector, since whoever's
        # voice is stored there is who future turns get attributed to.
        raise HTTPException(403, "You can only enroll your own voice")

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
async def finalize(speaker_id: int, db: AsyncSession = Depends(get_db),
                    authorization: str | None = Header(None)):
    user = await _current_user(authorization, db)
    e = (await db.execute(
        select(VoiceEnrollment).where(VoiceEnrollment.id == speaker_id)
    )).scalar_one_or_none()
    if not e:
        raise HTTPException(404)
    if e.user_id != user.id:
        raise HTTPException(403, "You can only finalize your own enrollment")
    e.status = "ready"
    await db.commit()
    return {"status": "ready", "voice_id": f"#{speaker_id:06X}"}


@router.delete("/{speaker_id}")
async def delete(speaker_id: int, db: AsyncSession = Depends(get_db),
                  authorization: str | None = Header(None)):
    user = await _current_user(authorization, db)
    e = (await db.execute(
        select(VoiceEnrollment).where(VoiceEnrollment.id == speaker_id)
    )).scalar_one_or_none()
    if e:
        if e.user_id != user.id and user.role != "admin":
            raise HTTPException(403, "You can only delete your own enrollment")
        await db.delete(e)
        await db.commit()
    return {"status": "deleted"}
