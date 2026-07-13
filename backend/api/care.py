"""
Customer-care dashboard actions.

In customercare mode PILOT is a silent observer — the voice tool-routing path
is disabled (see pipeline/asr_worker.py), so the CSR drives the two explicit
actions from dashboard buttons instead of by voice:

  - assess         : run the resolve/escalate engine (non-destructive)
  - submit_ticket  : create the ticket from the current call (destructive-gated)
  - escalate       : create an escalation ticket (destructive-gated)

Identity is taken from the caller's JWT — a logged-in CSR is a stronger identity
guarantee than a voice match, so destructive actions here are `pre_confirmed`
(they skip the *voice* confirmation latch) but still pass the normal RBAC role
check: a token whose role can't run the tool is denied exactly as a voice
attempt would be. Every action is audited through the same policy_gate path.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from db.engine import get_db
from db.models import User

logger = logging.getLogger("pilot.care")
router = APIRouter()

_ACTION_TOOL = {
    "assess":        "resolution_assess",
    "submit_ticket": "ticket_create",
    "escalate":      "escalate_ticket",
}


class CareActionReq(BaseModel):
    session_id: str
    action: str            # assess | submit_ticket | escalate
    synopsis: str | None = None
    category: str | None = None
    symptoms: str | None = None


def _claims_from_token(authorization: str | None) -> dict | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        from core.security import decode_token
        return decode_token(authorization.split(" ", 1)[1])
    except Exception:
        return None


@router.post("/action")
async def care_action(req: CareActionReq, db: AsyncSession = Depends(get_db),
                      authorization: str | None = Header(None)):
    tool = _ACTION_TOOL.get(req.action)
    if not tool:
        raise HTTPException(400, f"Unknown action '{req.action}'")

    claims = _claims_from_token(authorization)
    if not claims:
        raise HTTPException(401, "Authentication required")
    role = (claims.get("role") or "user").lower()

    # Real name for audit/identity (the JWT is the identity here, not voice).
    speaker_name = claims.get("email") or "csr"
    if claims.get("email"):
        user = (await db.execute(
            select(User).where(User.email == claims["email"])
        )).scalar_one_or_none()
        if user:
            speaker_name = user.name

    from tools.policy import policy_gate
    from tools.registry import TOOL_REGISTRY

    # Destructive actions are pre_confirmed (button click by an authenticated
    # CSR = confirmation); the assess action isn't destructive so this is a
    # no-op flag for it. RBAC still applies to all.
    allowed = await policy_gate.check(
        tool=tool, speaker_id=speaker_name, role=role,
        session_id=req.session_id, pre_confirmed=True,
    )
    if not allowed:
        from pipeline.front_llm import _min_required_level, _ROLE_LEVELS
        required = _min_required_level(tool)
        raise HTTPException(403, {
            "error": "access_denied",
            "your_level": _ROLE_LEVELS.get(role, 1),
            "required_level": required,
            "message": f"Your role ({role}) cannot perform this action.",
        })

    handler = TOOL_REGISTRY.get(tool)
    if not handler:
        raise HTTPException(500, f"No handler for '{tool}'")

    args = {
        "synopsis": req.synopsis or "",
        "category": req.category or "general",
        "symptoms": req.symptoms or "",
    }
    try:
        result = await handler(args, req.session_id)
    except Exception as e:
        logger.error(f"care action {req.action} failed: {e}", exc_info=True)
        raise HTTPException(500, f"Action failed: {e}")

    # Never surface a spoken_reply from a dashboard action — it's screen-only.
    result.pop("spoken_reply", None)
    logger.info(f"[{req.session_id[:8]}] care action '{req.action}' by {speaker_name} ({role}) → ok")
    return {"status": "ok", "action": req.action, **result}
