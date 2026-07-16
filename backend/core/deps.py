"""
backend/core/deps.py
Shared FastAPI dependency functions for authentication, user resolution, and rate limiting.
"""

import time
from collections import defaultdict

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import decode_token
from backend.db.engine import get_db
from backend.db.models import User


# ── Auth Dependencies ─────────────────────────────────────────────────────────

async def get_current_user_id(authorization: str = Header(...)) -> int:
    """Extracts user ID from JWT Bearer token. Raises HTTP 401 if invalid."""
    try:
        token = authorization.split(" ")[1]
        payload = decode_token(token)
        return int(payload["sub"])
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing Authorization token. Please log in again.",
        )


async def get_current_user(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolves full User ORM object. Raises 401 if not found, 403 if inactive."""
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User account not found.")
    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail="Account not yet verified. Please complete email OTP verification.",
        )
    return user


# ── Rate Limiter ──────────────────────────────────────────────────────────────

# In-memory per-user call timestamps: {user_id -> {"search": [...], "book": [...]}}
_rate_store: dict = defaultdict(lambda: {"search": [], "book": []})

SEARCH_LIMIT = 25   # max search requests per user per minute
BOOK_LIMIT   = 25    # max book requests per user per minute
WINDOW_S     = 60   # rolling window in seconds


def _check_rate(user_id: int, action: str, limit: int) -> None:
    """Raises HTTP 429 if the user exceeds the per-minute limit for `action`."""
    now = time.monotonic()
    calls = _rate_store[user_id][action]
    calls[:] = [t for t in calls if t > now - WINDOW_S]
    if len(calls) >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {limit} {action} requests per minute.",
        )
    calls.append(now)


class BookingRateLimiter:
    """
    FastAPI dependency enforcing per-user rate limits.
    Usage:
        _rl_search = BookingRateLimiter("search")
        @router.post("/search")
        async def search(req, user=Depends(get_current_user), _=Depends(_rl_search)):
            ...
    """
    def __init__(self, action: str):
        self.action = action
        self.limit = SEARCH_LIMIT if action == "search" else BOOK_LIMIT

    async def __call__(self, user: User = Depends(get_current_user)) -> None:
        _check_rate(user.id, self.action, self.limit)
