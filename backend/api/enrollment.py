import asyncio
import logging
import os
from typing import List

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.engine import get_db
from backend.db.models import VoiceEnrollment

"""
Enrollment API — mic recording + file upload → embedding → identity
Features: start, submit audio (mic or file), finalize, list, delete
"""


# Create Speaker
#       │
#       ▼
# Upload 3 Voice Samples
#       │
#       ▼
# Extract Embeddings
#       │
#       ▼
# Average Embeddings
#       │
#       ▼
# Save Embedding (.npy + Database)
#       │
#       ▼
# Enrollment Ready


router = APIRouter()
logger = logging.getLogger("pilot.enrollment")


class StartReq(BaseModel):
    name: str
    role: str


# Returns every completed speaker.
@router.get("")
async def list_speakers(db: AsyncSession = Depends(get_db)):
    rows = (
        (await db.execute(select(VoiceEnrollment).where(VoiceEnrollment.status == "ready"))).scalars().all()
    )
    return [{"id": r.id, "name": r.speaker_name, "role": r.role, "voice_id": f"#{r.id:06X}"} for r in rows]


# Begins a new voice enrollment process by registering a name and role.
@router.post("/start")
async def start(req: StartReq, db: AsyncSession = Depends(get_db)):
    e = VoiceEnrollment(user_id=None, speaker_name=req.name, role=req.role)
    db.add(e)  # add a new instance
    await db.commit()  # write the record and generates a uniqie , auto incremented primary key.
    await db.refresh(e)  # reflect actual changes
    return {"speaker_id": e.id, "name": req.name, "role": req.role}




@router.post("/audio")
async def submit_audio(
    speaker_id: str = Form(...),
    audio: List[UploadFile] = File(...),
    email: str = Form(None),
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Accepts multiple mic recordings (webm/wav), selects the best one, and creates User.

    Two ways this gets linked to a User:
    - `email` matching a PENDING_SIGNUPS entry: the normal signup wizard,
      where the account doesn't exist yet — the User row is created here.
    - A valid Bearer token: an already-authenticated user (e.g. Google SSO,
      which auto-registers with no voice sample of its own) completing
      enrollment separately via the /enroll page. Without this, that flow
      silently created an orphaned VoiceEnrollment row with no user_id ever
      set, so the account was never actually voice-identifiable.
    """
    authed_user_id: int | None = None
    if authorization:
        try:
            from backend.core.security import decode_token
            token = authorization.split(" ")[1]
            authed_user_id = int(decode_token(token)["sub"])
        except Exception:
            authed_user_id = None

    # queries the database to find the record matching the provided speaker_id .
    e = (
        await db.execute(select(VoiceEnrollment).where(VoiceEnrollment.id == int(speaker_id)))
    ).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Enrollment not found")

    # Extract embeddings for all 3 uploaded audio segments and calculate the Average (Mean) embedding vector
    import numpy as np

    from backend.services.enrollment import embed_provider

    embeddings = []
    for aud in audio:
        bytes_data = await aud.read()
        if len(bytes_data) < 1000:
            continue
        try:
            # Extract embedding from each audio pass
            emb = await asyncio.to_thread(embed_provider.extract, bytes_data)
            embeddings.append(emb)
        except Exception as ex:
            logger.warning(f"Failed to extract embedding from a registration pass: {ex}")

    if not embeddings:
        raise HTTPException(400, "No valid audio samples received or failed to extract embeddings")

    # Compute the average (mean) embedding vector to generalize pitch, resonance and prosody
    final_embedding = np.mean(embeddings, axis=0).astype(np.float16)
    # The database stores embeddings as a BLOB (binary large object), not as NumPy arrays
    emb_bytes = final_embedding.tobytes()

    # Create VoiceEnrollment directories
    os.makedirs("data/embeddings", exist_ok=True)
    npy_path = f"data/embeddings/speaker_{speaker_id}.npy"
    np.save(npy_path, final_embedding)

    # Check if this email corresponds to a pending signup session
    from backend.api.auth import PENDING_SIGNUPS
    from backend.db.models import User

    if email and email in PENDING_SIGNUPS:
        pending = PENDING_SIGNUPS.pop(email)
        user = User(
            name=pending["name"],
            email=pending["email"],
            hashed_pw=pending["hashed_pw"],
            role=pending["role"],
            is_active=True,
            embedding=emb_bytes,  # Best voice embedding saved directly as BLOB in SQLite users table
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        e.user_id = user.id
        logger.info(
            f"Pending user '{user.email}' registered successfully with ID={user.id} and best voice embedding."
        )
    elif authed_user_id:
        # Already-authenticated user (e.g. Google SSO) completing enrollment
        # separately from account creation — link it to their real account
        # instead of leaving user_id unset.
        existing_user = (await db.execute(select(User).where(User.id == authed_user_id))).scalar_one_or_none()
        if existing_user:
            e.user_id = existing_user.id
            existing_user.embedding = emb_bytes
            logger.info(f"Enrollment linked to existing user_id={existing_user.id} ({existing_user.email})")

    e.embedding = emb_bytes
    e.npy_path = npy_path
    e.status = "ready"
    await db.commit()
    voice_id = f"#{int(speaker_id):06X}"
    logger.info(f"Enrollment complete: {e.speaker_name} → {voice_id}")
    return {"status": "ready", "speaker_id": speaker_id, "voice_id": voice_id}


# manually marks an enrollment as completed and ready to use.
@router.post("/finalize/{speaker_id}")
async def finalize(speaker_id: int, db: AsyncSession = Depends(get_db)):
    e = (
        await db.execute(select(VoiceEnrollment).where(VoiceEnrollment.id == speaker_id))
    ).scalar_one_or_none()
    if not e:
        raise HTTPException(404)
    e.status = "ready"
    await db.commit()
    return {"status": "ready", "voice_id": f"#{speaker_id:06X}"}


# remove an enrollment permanantly.
@router.delete("/{speaker_id}")
async def delete(speaker_id: int, db: AsyncSession = Depends(get_db)):
    e = (
        await db.execute(select(VoiceEnrollment).where(VoiceEnrollment.id == speaker_id))
    ).scalar_one_or_none()
    if e:
        await db.delete(e)
        await db.commit()
    return {"status": "deleted"}
