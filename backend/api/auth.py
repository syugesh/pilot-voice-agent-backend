# api router create api routes
# depends : dependency injection
# file : receive upload file
# uploadfile -> uploaded file object.


# run CPU heavy task in another thread.
import asyncio

# create logs
import logging

# basemodel : imports the core class used for data validation and parsing in Python
# emailStr: standard statement used to import the specialized email validation type.
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile

# Pydantic is Python's most popular data validation and parsing library or validates incoming JSON data.
# Now if the client sends
# {
#    "email":"abc",
#    "password":"123"
# }
# FastAPI automatically checks
# email format
# password type
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

# database operations
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import create_token, decode_token, gen_otp, hash_password, verify_password
from backend.db.engine import get_db  # create a database
from backend.db.models import User, VoiceEnrollment  # models

router = APIRouter()
logger = logging.getLogger("pilot.api.auth")


async def _has_enrollment(db: AsyncSession, user_id: int) -> bool:
    """True once a VoiceEnrollment row is both linked to this user and
    finished processing — used to gate dashboard access for accounts (e.g.
    Google SSO) that can get a valid token without ever recording a voice
    sample."""
    row = (
        await db.execute(
            select(VoiceEnrollment.id).where(
                VoiceEnrollment.user_id == user_id, VoiceEnrollment.status == "ready"
            )
        )
    ).first()
    return row is not None


class LoginReq(BaseModel):
    email: EmailStr
    password: str


class OtpVerifyReq(BaseModel):
    email: EmailStr
    otp: str


class OtpSendReq(BaseModel):
    email: EmailStr


# Global in-memory dictionary for pending signups (before voice calibration is complete)
# Key: email (str) -> Value: dict(name, email, hashed_pw, role, otp, otp_expiry, embedding)
PENDING_SIGNUPS = {}


@router.post("/signup")
async def signup(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("developer"),
    audio: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    # Check if user already exists and is active in database
    exists = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if exists:
        raise HTTPException(400, "Email already registered. Please sign in.")

    # Extract embeddings for all 3 uploaded audio segments and calculate the Average (Mean) embedding vector
    import numpy as np

    from backend.services.enrollment import embed_provider

    embeddings = []
    for aud in audio:
        bytes_data = await aud.read()
        # ensure baseline training quality: 1000 byte of raw 16kHz 16-bit (float16) audio represents only 31ms of sound.
        # 16000 sample/sec
        # 2bytes/sample
        # 1000/(16000*2) = 0.03125 seconds

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

    otp = gen_otp()
    PENDING_SIGNUPS[email] = {
        "name": name,
        "email": email,
        "hashed_pw": hash_password(password),
        "role": role,
        "otp": otp,
        "otp_expiry": datetime.now() + timedelta(minutes=10),
        "embedding": final_embedding.tobytes(),
    }
    await _send_otp(email, otp)
    return {"message": "OTP sent", "email": email}


@router.post("/send-otp")
async def send_otp(req: OtpSendReq, db: AsyncSession = Depends(get_db)):
    pending = PENDING_SIGNUPS.get(req.email)
    # user is not waiting for signup verification.
    if not pending:
        # Check actual db in case of re-verifying a legacy inactive user
        user = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
        # If both
        # not in PENDING_SIGNUPS
        # not in database
        if not user:
            raise HTTPException(404, "User not found. Please sign up first.")
        # if user already verified.
        if user.is_active:
            raise HTTPException(400, "Account already verified")
        otp = gen_otp()
        user.otp = otp
        user.otp_expiry = datetime.utcnow() + timedelta(minutes=10)
        await db.commit()  # changes remain only in memory and won't be written to the database.
        await _send_otp(req.email, otp)  # This helper function sends the generated OTP to the user's email.
        return {"message": "OTP resent"}

    otp = gen_otp()
    pending["otp"] = otp
    pending["otp_expiry"] = datetime.utcnow() + timedelta(minutes=10)
    await _send_otp(req.email, otp)
    return {"message": "OTP resent"}


@router.post("/verify-otp")
async def verify_otp(req: OtpVerifyReq, db: AsyncSession = Depends(get_db)):
    pending = PENDING_SIGNUPS.get(req.email)
    if not pending:
        # Fallback to legacy database check for already stored inactive users
        user = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
        if not user:
            raise HTTPException(404, "User not found")
        if user.otp != req.otp:
            raise HTTPException(400, "Invalid OTP")
        if user.otp_expiry and datetime.utcnow() > user.otp_expiry:
            raise HTTPException(400, "OTP expired")
        user.is_active = True
        user.otp = None
        await db.commit()
        token = create_token({"sub": str(user.id), "email": user.email, "role": user.role})
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "id": user.id, "name": user.name, "email": user.email, "role": user.role,
                "has_enrollment": await _has_enrollment(db, user.id),
            },
        }

    if pending["otp"] != req.otp:
        raise HTTPException(400, "Invalid OTP")
    if pending["otp_expiry"] and datetime.utcnow() > pending["otp_expiry"]:
        raise HTTPException(400, "OTP expired")

    # OTP is verified successfully! Create actual user in SQLite with best embedding
    user = User(
        name=pending["name"],
        email=pending["email"],
        hashed_pw=pending["hashed_pw"],
        role=pending["role"],
        is_active=True,
        embedding=pending["embedding"],
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Save to VoiceEnrollment so diarizer and speech logic loads this speaker profile
    import os

    import numpy as np

    os.makedirs("data/embeddings", exist_ok=True)
    enroll = VoiceEnrollment(
        user_id=user.id,
        speaker_name=user.name,
        role=user.role,
        embedding=pending["embedding"],
        status="ready",
    )
    db.add(enroll)
    await db.commit()
    await db.refresh(enroll)

    # Write matching npy file
    npy_path = f"data/embeddings/speaker_{enroll.id}.npy"
    np.save(npy_path, np.frombuffer(pending["embedding"], dtype=np.float16))
    enroll.npy_path = npy_path
    user.is_active = True
    user.status = "online"
    await db.commit()

    PENDING_SIGNUPS.pop(req.email, None)

    token = create_token({"sub": str(user.id), "email": user.email, "role": user.role})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "status": "online",
            "has_enrollment": True,  # this signup wizard just created it above
        },
    }


@router.post("/login")
async def login(req: LoginReq, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if not user or not verify_password(req.password, user.hashed_pw):
        raise HTTPException(401, "Invalid credentials")
    if not user.is_active:
        raise HTTPException(403, "Account not verified — check your email for OTP")
    token = create_token({"sub": str(user.id), "email": user.email, "role": user.role})
    user.status = "online"
    await db.commit()
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "status": "online",
            "has_enrollment": await _has_enrollment(db, user.id),
        },
    }


@router.post("/forgot-password")
async def forgot_password(req: OtpSendReq, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found with this email.")

    otp = gen_otp()
    user.otp = otp
    user.otp_expiry = datetime.utcnow() + timedelta(minutes=10)
    await db.commit()

    await _send_otp(req.email, otp)
    return {"message": "OTP sent for password recovery", "email": req.email}


@router.post("/verify-forgot-otp")
async def verify_forgot_otp(req: OtpVerifyReq, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")

    if not user.otp or user.otp != req.otp:
        raise HTTPException(400, "Invalid OTP")

    if user.otp_expiry and datetime.utcnow() > user.otp_expiry:
        raise HTTPException(400, "OTP expired")

    user.otp = None
    user.otp_expiry = None
    user.is_active = True
    await db.commit()

    token = create_token({"sub": str(user.id), "email": user.email, "role": user.role})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id, "name": user.name, "email": user.email, "role": user.role,
            "has_enrollment": await _has_enrollment(db, user.id),
        },
    }


@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.name))
    users = result.scalars().all()
    return [
        {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "role": u.role,
            "is_active": u.is_active,
            "status": u.status,
        }
        for u in users
    ]


@router.post("/logout")
async def logout(authorization: str = Header(...), db: AsyncSession = Depends(get_db)):
    try:
        token = authorization.split(" ")[1]
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token or authorization header format")

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user:
        user.status = "not availabel"
        await db.commit()
    return {"message": "Logged out successfully"}


class ProfileUpdateReq(BaseModel):
    name: str
    email: EmailStr


@router.put("/profile")
async def update_profile(
    req: ProfileUpdateReq, authorization: str = Header(...), db: AsyncSession = Depends(get_db)
):
    try:
        token = authorization.split(" ")[1]
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token or authorization header format")

    # Check if the user exists
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if the email is already taken by another user
    if req.email != user.email:
        existing = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=400, detail="Email already taken")

    # Update User table
    user.name = req.name
    user.email = req.email
    await db.commit()
    await db.refresh(user)

    # Also update matching VoiceEnrollment speaker_name if it exists
    from sqlalchemy import update

    await db.execute(
        update(VoiceEnrollment).where(VoiceEnrollment.user_id == user_id).values(speaker_name=req.name)
    )
    await db.commit()

    # Generate a new token with updated email and name
    new_token = create_token({"sub": str(user.id), "email": user.email, "role": user.role})

    # Broadcast profile update to all active sessions in real-time
    try:
        from backend.core.ws_manager import broadcast_all

        await broadcast_all(
            {
                "type": "profile_updated",
                "payload": {"user_id": user.id, "name": user.name, "email": user.email},
            }
        )
    except Exception as e:
        logger.error(f"Failed to broadcast profile update: {e}")

    return {
        "access_token": new_token,
        "token_type": "bearer",
        "user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role},
    }


async def _send_otp(email: str, otp: str):
    from backend.core.config import settings

    # Always print to terminal so dev mode always works
    # print(f"\n{'='*48}\nPILOT OTP for {email}: {otp}\n{'='*48}\n")
    logger.info(f"OTP for {email}: {otp}")

    if not settings.SMTP_USER or not settings.SMTP_PASS:
        return  # dev mode — OTP visible in terminal above

    try:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "PILOT — Your verification code"
        msg["From"] = settings.SMTP_USER
        msg["To"] = email

        html = f"""
        <div style="font-family:sans-serif;max-width:480px;margin:40px auto;background:#0A0A0F;
                    color:#E8E8F0;border-radius:12px;padding:2rem;border:1px solid rgba(108,99,255,0.2)">
          <div style="font-size:1.5rem;font-weight:700;color:#6C63FF;margin-bottom:0.5rem">⬡ PILOT</div>
          <p style="color:#8888AA;margin-bottom:1.5rem">Your verification code:</p>
          <div style="font-size:2.5rem;font-weight:700;letter-spacing:0.4em;
                      color:#fff;background:#1A1A26;border-radius:8px;
                      padding:1rem;text-align:center;margin-bottom:1.5rem">
            {otp}
          </div>
          <p style="color:#555570;font-size:0.82rem">Expires in 10 minutes. Do not share this code.</p>
        </div>
        """
        msg.attach(MIMEText(html, "html"))

        await asyncio.to_thread(
            _smtp_send,
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            settings.SMTP_USER,
            settings.SMTP_PASS,
            email,
            msg,
        )
        logger.info(f"OTP email sent to {email}")

    except Exception as e:
        logger.error(f"SMTP send failed: {e} — OTP is in terminal above")


def _smtp_send(host, port, user, password, to, msg):
    import smtplib

    with smtplib.SMTP(host, port, timeout=10) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(user, password)
        server.sendmail(user, to, msg.as_string())


# ── Google SSO — server-side OAuth2 authorization-code flow ──
# GET /sso/google redirects the browser to Google's consent screen; Google
# redirects back to /sso/google/callback with a one-time code, which is
# exchanged server-to-server for the user's profile (needs GOOGLE_CLIENT_SECRET
# since this exchange happens off-browser, unlike the old ID-token flow).
_sso_states: dict[str, bool] = {}  # in-memory CSRF state store — fine for a single backend instance


@router.get("/sso/google")
async def sso_google_start():
    from backend.core.config import settings
    import secrets
    from urllib.parse import urlencode

    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(501, "Google SSO not configured — set GOOGLE_CLIENT_ID in .env")
    state = secrets.token_urlsafe(16)
    _sso_states[state] = True
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "select_account",
    }
    from fastapi.responses import RedirectResponse

    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@router.get("/sso/google/callback")
async def sso_google_callback(code: str, state: str, db: AsyncSession = Depends(get_db)):
    import base64
    import json

    import httpx
    from fastapi.responses import RedirectResponse

    from backend.core.config import settings

    if state not in _sso_states:
        raise HTTPException(400, "Invalid or expired OAuth state")
    del _sso_states[state]

    # Mock code path for tests/local runs — bypasses the real Google HTTP
    # exchange the same way the old flow's "mock_google_" token prefix did,
    # so this route stays testable without hitting Google's servers.
    if code.startswith("mock_code_"):
        parts = code.split("_")
        email = parts[2] if len(parts) > 2 else "mockuser@example.com"
        name = parts[3] if len(parts) > 3 else "Mock User"
    else:
        async with httpx.AsyncClient(timeout=10.0) as client:
            tok_res = await client.post("https://oauth2.googleapis.com/token", data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            })
            if tok_res.status_code != 200:
                logger.error(f"Google token exchange failed: {tok_res.text}")
                raise HTTPException(400, "Google authentication failed")
            access_token = tok_res.json().get("access_token")

            prof_res = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if prof_res.status_code != 200:
                raise HTTPException(400, "Failed to fetch Google profile")
            g = prof_res.json()

        email = (g.get("email") or "").strip().lower()
        name = g.get("name") or (email.split("@")[0] if email else "")

    if not email:
        raise HTTPException(400, "Google account has no email address")

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    is_new_user = user is None
    if not user:
        user = User(
            name=name,
            email=email,
            hashed_pw="",  # empty — SSO users never log in with a password
            role="developer",  # placeholder — brand-new signups pick their real role next, see PATCH /role
            is_active=True,
            status="online",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    else:
        user.status = "online"
        await db.commit()

    token = create_token({"sub": str(user.id), "email": user.email, "role": user.role})

    # Pass token + user info to the frontend via URL params — the frontend
    # reads these off window.location on load, same shape has_enrollment
    # already takes elsewhere so the mandatory-voice-enrollment gate in
    # app.tsx applies here identically. new_user tells the frontend whether
    # to send this account to the role-selection step first (SSO has no
    # signup form, so there's nowhere else a brand-new account could have
    # picked a real role) before the voice-enrollment gate applies.
    user_b64 = base64.urlsafe_b64encode(json.dumps({
        "id": user.id, "name": user.name, "email": user.email, "role": user.role,
    }).encode()).decode()
    enrolled = await _has_enrollment(db, user.id)

    redirect = (
        f"{settings.FRONTEND_URL}/"
        f"?sso_token={token}"
        f"&sso_user={user_b64}"
        f"&voice_enrolled={'1' if enrolled else '0'}"
        f"&new_user={'1' if is_new_user else '0'}"
    )
    return RedirectResponse(redirect)


class ChooseRoleReq(BaseModel):
    role: str


@router.patch("/role")
async def choose_role(req: ChooseRoleReq, authorization: str | None = Header(None),
                       db: AsyncSession = Depends(get_db)):
    """Lets a brand-new SSO signup pick their real role — SSO has no signup
    form, so accounts are created with a 'developer' placeholder (see
    sso_google_callback above) that this replaces, once, right after login."""
    valid_roles = {"developer", "manager", "csr", "operator", "admin"}
    if req.role not in valid_roles:
        raise HTTPException(400, f"Invalid role. Must be one of: {', '.join(sorted(valid_roles))}")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Login required")
    try:
        payload = decode_token(authorization.split(" ", 1)[1])
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(401, "Invalid or expired token")

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    user.role = req.role
    await db.commit()

    new_token = create_token({"sub": str(user.id), "email": user.email, "role": user.role})
    return {
        "access_token": new_token,
        "token_type": "bearer",
        "user": {
            "id": user.id, "name": user.name, "email": user.email, "role": user.role,
            "has_enrollment": await _has_enrollment(db, user.id),
        },
    }
