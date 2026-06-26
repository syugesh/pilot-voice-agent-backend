from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta
import asyncio
import logging
import secrets
import base64
import json
import uuid

import httpx
from urllib.parse import urlencode

from db.engine import get_db
from db.models import User
from core.security import hash_password, verify_password, create_token, gen_otp
from core.config import settings

router = APIRouter()
logger = logging.getLogger("pilot.api.auth")

# In-memory state tokens for OAuth CSRF protection (ephemeral, fine for single-instance)
_sso_states: dict[str, bool] = {}

# Pending registrations — user data stored here until OTP verified, then inserted to DB
_pending: dict[str, dict] = {}  # email → {name, hashed_pw, role, otp, otp_expiry}


class SignupReq(BaseModel):
    name: str; email: EmailStr; password: str; role: str = "developer"

class LoginReq(BaseModel):
    email: EmailStr; password: str

class OtpVerifyReq(BaseModel):
    email: EmailStr; otp: str

class OtpSendReq(BaseModel):
    email: EmailStr


@router.post("/signup")
async def signup(req: SignupReq, db: AsyncSession = Depends(get_db)):
    # Reject if already an active account in DB
    exists = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if exists and exists.is_active:
        raise HTTPException(400, "Email already registered. Please sign in.")

    otp    = gen_otp()
    expiry = datetime.utcnow() + timedelta(minutes=10)

    # Store pending — no DB insert until OTP verified
    _pending[req.email] = {
        "name":       req.name,
        "hashed_pw":  hash_password(req.password),
        "role":       req.role,
        "otp":        otp,
        "otp_expiry": expiry,
    }
    await _send_otp(req.email, otp)
    return {"message": "OTP sent", "email": req.email}


@router.post("/send-otp")
async def send_otp(req: OtpSendReq, db: AsyncSession = Depends(get_db)):
    otp    = gen_otp()
    expiry = datetime.utcnow() + timedelta(minutes=10)

    # Pending user (not yet in DB)
    if req.email in _pending:
        _pending[req.email]["otp"]        = otp
        _pending[req.email]["otp_expiry"] = expiry
        await _send_otp(req.email, otp)
        return {"message": "OTP resent"}

    # Existing inactive DB user (edge case — old records)
    user = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    user.otp        = otp
    user.otp_expiry = expiry
    await db.commit()
    await _send_otp(req.email, otp)
    return {"message": "OTP resent"}


@router.post("/verify-otp")
async def verify_otp(req: OtpVerifyReq, db: AsyncSession = Depends(get_db)):
    # --- Path 1: pending registration (no DB entry yet) ---
    pending = _pending.get(req.email)
    if pending:
        if pending["otp"] != req.otp:
            raise HTTPException(400, "Invalid OTP")
        if datetime.utcnow() > pending["otp_expiry"]:
            del _pending[req.email]
            raise HTTPException(400, "OTP expired")
        # OTP valid — now create the user in DB for the first time
        user = User(
            name=pending["name"],
            email=req.email,
            hashed_pw=pending["hashed_pw"],
            role=pending["role"],
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        del _pending[req.email]
        token = create_token({"sub": str(user.id), "email": user.email, "role": user.role})
        return {"access_token": token, "token_type": "bearer",
                "voice_enrolled": False,
                "user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role}}

    # --- Path 2: existing inactive DB user (legacy / re-verify) ---
    user = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    if user.otp != req.otp:
        raise HTTPException(400, "Invalid OTP")
    if user.otp_expiry and datetime.utcnow() > user.otp_expiry:
        raise HTTPException(400, "OTP expired")
    user.is_active = True
    user.otp       = None
    await db.commit()
    token = create_token({"sub": str(user.id), "email": user.email, "role": user.role})
    from db.models import VoiceEnrollment
    ve = (await db.execute(
        select(VoiceEnrollment).where(
            VoiceEnrollment.user_id == user.id,
            VoiceEnrollment.status == "ready"
        )
    )).scalar_one_or_none()
    return {"access_token": token, "token_type": "bearer",
            "voice_enrolled": ve is not None,
            "user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role}}


@router.post("/login")
async def login(req: LoginReq, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if not user or not verify_password(req.password, user.hashed_pw):
        raise HTTPException(401, "Invalid credentials")
    if not user.is_active:
        raise HTTPException(403, "Account not verified — check your email for OTP")
    token = create_token({"sub": str(user.id), "email": user.email, "role": user.role})
    from db.models import VoiceEnrollment
    ve = (await db.execute(
        select(VoiceEnrollment).where(
            VoiceEnrollment.user_id == user.id,
            VoiceEnrollment.status == "ready"
        )
    )).scalar_one_or_none()
    return {"access_token": token, "token_type": "bearer",
            "voice_enrolled": ve is not None,
            "user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role}}


async def _send_otp(email: str, otp: str):
    from core.config import settings
    # Only log at DEBUG level — never print OTP to console in production
    logger.debug(f"OTP generated for {email}")

    if not settings.SMTP_USER or not settings.SMTP_PASS:
        # No SMTP configured — raise clear error so operator knows to configure it
        logger.error(
            "SMTP not configured. Set SMTP_USER and SMTP_PASS in backend/.env\n"
            "Gmail: use an App Password from myaccount.google.com/security"
        )
        raise RuntimeError("Email service not configured. Please contact your administrator.")

    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "PILOT — Your verification code"
        msg["From"]    = settings.SMTP_USER
        msg["To"]      = email

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
            _smtp_send, settings.SMTP_HOST, settings.SMTP_PORT,
            settings.SMTP_USER, settings.SMTP_PASS, email, msg
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


# ── Google SSO ──

@router.get("/sso/google")
async def sso_google_start():
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
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@router.get("/sso/google/callback")
async def sso_google_callback(code: str, state: str, db: AsyncSession = Depends(get_db)):
    if state not in _sso_states:
        raise HTTPException(400, "Invalid or expired OAuth state")
    del _sso_states[state]

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Exchange authorization code for access token
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

        # Fetch user profile
        prof_res = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if prof_res.status_code != 200:
            raise HTTPException(400, "Failed to fetch Google profile")
        g = prof_res.json()

    email   = g.get("email", "")
    name    = g.get("name") or email.split("@")[0]
    g_id    = g.get("id", "")

    if not email:
        raise HTTPException(400, "Google account has no email address")

    # Find or create user
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not user:
        user = User(
            name=name, email=email,
            hashed_pw=hash_password(str(uuid.uuid4())),  # random — SSO users never use password login
            role="developer",
            oauth_provider="google", oauth_id=g_id,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info(f"New Google SSO user: {email}")
    else:
        if not user.oauth_provider:
            user.oauth_provider = "google"
            user.oauth_id = g_id
        user.is_active = True
        await db.commit()

    jwt = create_token({"sub": str(user.id), "email": user.email, "role": user.role})

    from db.models import VoiceEnrollment
    ve = (await db.execute(
        select(VoiceEnrollment).where(
            VoiceEnrollment.user_id == user.id,
            VoiceEnrollment.status == "ready",
        )
    )).scalar_one_or_none()

    # Pass token and user info to frontend via URL params
    user_b64 = base64.urlsafe_b64encode(json.dumps({
        "id": user.id, "name": user.name, "email": user.email, "role": user.role,
    }).encode()).decode()

    redirect = (
        f"{settings.FRONTEND_URL}/"
        f"?sso_token={jwt}"
        f"&sso_user={user_b64}"
        f"&voice_enrolled={'1' if ve else '0'}"
    )
    return RedirectResponse(redirect)
