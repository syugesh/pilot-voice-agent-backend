"""
Customer Resolution API — manual dashboard triggers for the voice-tool-driven
resolution_assess / escalate_ticket, so the CSR dashboard's "Assess Now" and
"Escalate" buttons work without requiring the rep to say it out loud. Voice
("should I escalate this?") still works via the normal tool-registry path;
this just gives the dashboard a second way in, per resolution_tool.py's
"Invoked like any other tool (voice ... or a dashboard button)" design.
"""
import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("pilot.api.resolution")
router = APIRouter()


class AssessReq(BaseModel):
    session_id: str


class EscalateReq(BaseModel):
    session_id: str
    synopsis: Optional[str] = None
    category: Optional[str] = None
    symptoms: Optional[str] = None
    priority: Optional[str] = None
    escalation_target: Optional[str] = None


@router.post("/assess")
async def assess(req: AssessReq):
    from backend.tools.resolution_tool import resolution_assess

    return await resolution_assess({}, req.session_id)


@router.post("/escalate")
async def escalate(req: EscalateReq):
    from backend.tools.resolution_tool import escalate_ticket

    args = {k: v for k, v in req.model_dump(exclude={"session_id"}).items() if v is not None}
    return await escalate_ticket(args, req.session_id)
