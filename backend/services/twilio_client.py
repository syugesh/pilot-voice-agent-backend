"""
Twilio Voice client — real two-party calling for Customer Resolution. The
rep talks through the browser (Twilio Voice JS SDK / WebRTC); Twilio bridges
that to a real PSTN call to the customer's phone number.

Two distinct credential pairs are involved, per Twilio's own model:
- ACCOUNT_SID/AUTH_TOKEN: account-level REST API credentials.
- API_KEY_SID/API_KEY_SECRET: a separate key pair Twilio requires
  specifically for *signing* client-side Voice access tokens — the account
  credentials alone can't do this.
"""
import logging

from backend.core.config import settings

logger = logging.getLogger("pilot.twilio")


def is_configured() -> bool:
    return bool(
        settings.TWILIO_ACCOUNT_SID
        and settings.TWILIO_API_KEY_SID
        and settings.TWILIO_API_KEY_SECRET
        and settings.TWILIO_TWIML_APP_SID
    )


def generate_voice_access_token(identity: str) -> str:
    """A short-lived JWT the rep's browser uses to register as a Twilio Voice
    JS SDK client under `identity`, and to authorize outbound calls routed
    to our TwiML App (see api/telephony.py's /voice webhook)."""
    from twilio.jwt.access_token import AccessToken
    from twilio.jwt.access_token.grants import VoiceGrant

    token = AccessToken(
        settings.TWILIO_ACCOUNT_SID,
        settings.TWILIO_API_KEY_SID,
        settings.TWILIO_API_KEY_SECRET,
        identity=identity,
        ttl=3600,
    )
    voice_grant = VoiceGrant(
        outgoing_application_sid=settings.TWILIO_TWIML_APP_SID,
        incoming_allow=True,
    )
    token.add_grant(voice_grant)
    return token.to_jwt()


def validate_twilio_request(url: str, params: dict, signature: str) -> bool:
    """Verifies a webhook request actually came from Twilio (not a spoofed
    POST to our public TwiML endpoint) using the account auth token."""
    from twilio.request_validator import RequestValidator

    if not settings.TWILIO_AUTH_TOKEN:
        logger.warning("TWILIO_AUTH_TOKEN not set — cannot validate webhook signature")
        return False
    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    return validator.validate(url, params, signature)
