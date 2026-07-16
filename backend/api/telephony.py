"""
Telephony API — real two-party calling for Customer Resolution.

Flow: rep clicks "Call Customer" in the browser -> fetches a Voice access
token from /token -> Twilio Voice JS SDK places a WebRTC call authorized by
that token -> Twilio POSTs to /voice (this account's TwiML App webhook) ->
we return TwiML that (a) starts streaming live call audio to our
ws/telephony WebSocket for real-time transcription/sentiment, and (b) dials
the customer's real phone number, bridging the two legs together.
"""
import logging
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response

from backend.core.deps import get_current_user
from backend.db.models import User

logger = logging.getLogger("pilot.api.telephony")
router = APIRouter()

_E164_RE = re.compile(r"^\+?[1-9]\d{7,14}$")


@router.get("/token")
async def token(user: User = Depends(get_current_user)):
    """Voice access token for the authenticated rep's browser client."""
    from backend.services.twilio_client import is_configured, generate_voice_access_token

    if not is_configured():
        raise HTTPException(
            503,
            "Telephony isn't configured yet — set TWILIO_ACCOUNT_SID, TWILIO_API_KEY_SID, "
            "TWILIO_API_KEY_SECRET, and TWILIO_TWIML_APP_SID in backend/.env.",
        )
    identity = f"rep_{user.id}"
    return {"token": generate_voice_access_token(identity), "identity": identity}


@router.post("/voice")
async def voice_webhook(request: Request):
    """TwiML App webhook — Twilio calls this the moment the rep's browser
    client places an outbound call. Not user-facing; verified as a genuine
    Twilio request via the account auth token before acting on it."""
    from backend.core.config import settings
    from backend.services.twilio_client import validate_twilio_request

    form = await request.form()
    params = dict(form)

    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)
    if settings.TWILIO_AUTH_TOKEN and not validate_twilio_request(url, params, signature):
        logger.warning(f"Rejected /telephony/voice webhook with invalid Twilio signature from {request.client}")
        raise HTTPException(403, "Invalid Twilio signature")

    to_number = (params.get("To") or "").strip()
    session_id = (params.get("session_id") or "").strip()
    from_identity = (params.get("From") or "").strip()  # e.g. "client:rep_12"

    if not to_number or not _E164_RE.match(to_number):
        twiml = '<?xml version="1.0" encoding="UTF-8"?><Response><Say>Invalid phone number.</Say><Hangup/></Response>'
        return Response(content=twiml, media_type="application/xml")

    if not session_id:
        twiml = '<?xml version="1.0" encoding="UTF-8"?><Response><Say>Missing session.</Say><Hangup/></Response>'
        return Response(content=twiml, media_type="application/xml")

    # Ensure a real customercare session exists for this call, matching the
    # same usecase Customer Resolution's sentiment/resolution engine already
    # keys off of for browser-mic sessions.
    from backend.core.session_manager import session_manager
    user_id = None
    m = re.match(r"client:rep_(\d+)", from_identity)
    if m:
        user_id = int(m.group(1))
    if not session_manager.get(session_id):
        session_manager.register(session_id, user_id or 0, "customercare")
        from backend.db.engine import AsyncSessionLocal
        from backend.db.models import Session as PilotSession
        async with AsyncSessionLocal() as db:
            db.add(PilotSession(session_id=session_id, usecase="customercare", user_id=user_id))
            await db.commit()

    ws_scheme = "wss" if settings.PUBLIC_BASE_URL.startswith("https") else "ws"
    ws_host = settings.PUBLIC_BASE_URL.split("://", 1)[-1]
    stream_url = f"{ws_scheme}://{ws_host}/ws/telephony/{session_id}"

    caller_id = settings.TWILIO_PHONE_NUMBER or ""
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Start>
    <Stream url="{stream_url}" track="both_tracks">
      <Parameter name="session_id" value="{session_id}" />
    </Stream>
  </Start>
  <Dial callerId="{caller_id}">
    <Number>{to_number}</Number>
  </Dial>
</Response>"""
    return Response(content=twiml, media_type="application/xml")
