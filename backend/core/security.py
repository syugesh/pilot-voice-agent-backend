import bcrypt, secrets
from datetime import datetime, timedelta
from jose import jwt
from core.config import settings
import random, string

_ACCESS_EXPIRE_MIN   = 15
_REFRESH_EXPIRE_DAYS = 7


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_token(data: dict) -> str:
    payload = {**data, "type": "access",
               "exp": datetime.utcnow() + timedelta(minutes=_ACCESS_EXPIRE_MIN)}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(data: dict) -> str:
    """Rotating refresh token — HttpOnly cookie, 7-day expiry, unique jti for revocation."""
    payload = {**data, "type": "refresh",
               "jti": secrets.token_hex(16),
               "exp": datetime.utcnow() + timedelta(days=_REFRESH_EXPIRE_DAYS)}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def gen_otp(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))
