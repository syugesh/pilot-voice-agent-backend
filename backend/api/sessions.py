import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.engine import get_db
from backend.db.models import Session as PilotSession

logger = logging.getLogger("pilot.sessions")
router = APIRouter()


class CreateSessionReq(BaseModel):
    usecase: str  # ppt | customercare


@router.post("")
async def create(
    req: CreateSessionReq, authorization: Optional[str] = Header(None), db: AsyncSession = Depends(get_db)
):
    user_id = None
    if authorization and authorization.startswith("Bearer "):
        try:
            token = authorization.split(" ")[1]
            from backend.core.security import decode_token

            payload = decode_token(token)
            user_id = int(payload["sub"])
        except Exception as e:
            logger.warning(f"Failed to decode token for session creation: {e}")

    sid = str(uuid.uuid4())
    db.add(PilotSession(session_id=sid, usecase=req.usecase, user_id=user_id))
    await db.commit()
    from backend.core.session_manager import session_manager

    session_manager.register(sid, user_id or 0, req.usecase)
    return {"session_id": sid, "usecase": req.usecase, "state": "IDLE"}


@router.get("/list")
async def list_sessions(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import desc

    # Return only the top 6 most recent sessions ordered chronologically by ID to prevent dashboard clutter
    result = await db.execute(select(PilotSession).order_by(desc(PilotSession.id)).limit(6))
    rows = result.scalars().all()
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "usecase": s.usecase,
                "state": s.state,
                "created_at": str(s.created_at),
                "summary": s.summary,
                "bullets": s.bullets,
            }
            for s in rows
        ]
    }


@router.get("/stats")
async def session_stats(db: AsyncSession = Depends(get_db)):
    """Aggregate dashboard tiles — sessions/transcripts/tools today vs
    yesterday, and today's average tool latency. Real SQL aggregation over
    Session/TranscriptLog/AuditLog, not client-derived estimates, since the
    dashboard only ever sees the last 6 sessions via /list."""
    import datetime

    from sqlalchemy import func

    from backend.db.models import AuditLog, TranscriptLog

    now = datetime.datetime.utcnow()
    today_start = datetime.datetime(now.year, now.month, now.day)
    yesterday_start = today_start - datetime.timedelta(days=1)

    async def _count_since(model, ts_col, since, until=None):
        q = select(func.count()).select_from(model).where(ts_col >= since)
        if until is not None:
            q = q.where(ts_col < until)
        return (await db.execute(q)).scalar() or 0

    def _delta_pct(today: int, yesterday: int):
        if yesterday == 0:
            return None  # avoid a misleading "+inf%" — frontend shows "—" instead
        return round((today - yesterday) / yesterday * 100, 1)

    sessions_today = await _count_since(PilotSession, PilotSession.created_at, today_start)
    sessions_yesterday = await _count_since(PilotSession, PilotSession.created_at, yesterday_start, today_start)

    transcripts_today = await _count_since(TranscriptLog, TranscriptLog.created_at, today_start)
    transcripts_yesterday = await _count_since(TranscriptLog, TranscriptLog.created_at, yesterday_start, today_start)

    # PILOT's policy gate logs every tool-permission check as a
    # "policy_check" AuditLog row (see tools/policy.py's _audit()) — that's
    # the closest real signal to "tool activity today" this schema has.
    tools_today = (await db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.action == "policy_check", AuditLog.timestamp >= today_start)
    )).scalar() or 0
    tools_yesterday = (await db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.action == "policy_check", AuditLog.timestamp >= yesterday_start, AuditLog.timestamp < today_start)
    )).scalar() or 0

    # Actual per-tool-call latency is recorded separately, under
    # action="tool_call" (see core/bg_supervisor.py's finally-block audit
    # write, which is the only place latency_ms is ever populated) — not
    # under "policy_check", which never sets it and would always average to
    # None.
    avg_latency_today = (await db.execute(
        select(func.avg(AuditLog.latency_ms)).select_from(AuditLog)
        .where(AuditLog.action == "tool_call", AuditLog.timestamp >= today_start, AuditLog.latency_ms.is_not(None))
    )).scalar()

    return {
        "sessions_today": sessions_today, "sessions_delta_pct": _delta_pct(sessions_today, sessions_yesterday),
        "transcripts_today": transcripts_today, "transcripts_delta_pct": _delta_pct(transcripts_today, transcripts_yesterday),
        "tools_today": tools_today, "tools_delta_pct": _delta_pct(tools_today, tools_yesterday),
        "avg_latency_ms": round(avg_latency_today) if avg_latency_today is not None else None,
    }


@router.get("/{session_id}")
async def get(session_id: str, db: AsyncSession = Depends(get_db)):
    s = (
        await db.execute(select(PilotSession).where(PilotSession.session_id == session_id))
    ).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Session not found")
    return {
        "session_id": s.session_id,
        "usecase": s.usecase,
        "state": s.state,
        "summary": s.summary,
        "bullets": s.bullets,
    }


@router.get("/{session_id}/history")
async def get_history(session_id: str, db: AsyncSession = Depends(get_db)):
    from backend.db.models import AuditLog, TranscriptLog

    s = (
        await db.execute(select(PilotSession).where(PilotSession.session_id == session_id))
    ).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Session not found")

    transcripts_result = await db.execute(
        select(TranscriptLog).where(TranscriptLog.session_id == session_id).order_by(TranscriptLog.timestamp)
    )
    transcripts = transcripts_result.scalars().all()

    actions_result = await db.execute(
        select(AuditLog).where(AuditLog.session_id == session_id).order_by(AuditLog.timestamp)
    )
    actions = actions_result.scalars().all()

    return {
        "session": {
            "session_id": s.session_id,
            "usecase": s.usecase,
            "state": s.state,
            "created_at": str(s.created_at),
            "summary": s.summary,
            "bullets": s.bullets,
        },
        "transcripts": [
            {"speaker": t.speaker_id, "role": t.role, "text": t.text, "timestamp": t.timestamp}
            for t in transcripts
        ],
        "actions": [{"tool": a.tool, "decision": a.decision, "latency_ms": a.latency_ms} for a in actions],
    }


@router.delete("/{session_id}")
async def end(session_id: str, db: AsyncSession = Depends(get_db)):
    import json
    from datetime import datetime

    from sqlalchemy import select

    s = (
        await db.execute(select(PilotSession).where(PilotSession.session_id == session_id))
    ).scalar_one_or_none()
    if s:
        s.ended_at = datetime.utcnow()
        s.state = "ENDED"

        # ── AI-GENERATED CONVERSATION SUMMARY & BULLET POINTS ──
        # Generate summary and action points based on all transcript logs from this session
        from backend.db.models import TranscriptLog

        logs_result = await db.execute(
            select(TranscriptLog)
            .where(TranscriptLog.session_id == session_id)
            .order_by(TranscriptLog.timestamp)
        )
        logs = logs_result.scalars().all()

        # Extract background agent task data fully without summarization (all flights prices, code, tickets, etc.)
        #         The Problem: When an LLM summarizes a meeting, it naturally condenses and truncates technical details. If you generated a Python script or searched for 5 flight ticket options, a standard LLM summary would say something generic like "The assistant generated code and searched for flights," losing the actual code and ticket details completely.
        # The Solution: The endpoint scans the transcript logs and identifies lines containing code blocks ("```") or travel-booking keywords. It pulls these out into a separate bg_data_blocks list to bypass the LLM summarizer entirely, ensuring this critical technical data is never lost or compressed.
        bg_data_blocks = []
        for log in logs:
            text = log.text
            is_bg_task = False
            # Check for code blocks
            if "```" in text:
                is_bg_task = True
            # Check for flight lists/prices
            elif any(
                kw in text.lower()
                for kw in [
                    "akasa air",
                    "indigo",
                    "spicejet",
                    "air india",
                    "vistara",
                    "flight option",
                    "booking ref",
                    "found",
                    "departing",
                ]
            ):
                is_bg_task = True

            if is_bg_task and text not in bg_data_blocks:
                bg_data_blocks.append(text)

        if logs:
            # For general discussion, only pass non-background logs to the LLM to summarize
            general_discussion_logs = [
                log
                for log in logs
                if not (
                    "```" in log.text
                    or any(
                        kw in log.text.lower()
                        for kw in [
                            "akasa air",
                            "indigo",
                            "spicejet",
                            "air india",
                            "vistara",
                            "flight option",
                            "booking ref",
                            "found",
                            "departing",
                        ]
                    )
                )
            ]

            #             The Solution: By removing the huge technical blocks from the transcript text sent to the LLM (conversation_text), the system:
            # Reduces API Token Consumption: Avoids sending hundreds of lines of raw code or repetitive flight tables to the Groq API.
            # Avoids Summarizer Confusion: Raw code and API dumps can pollute the context, causing the LLM to write a dry, technical summary. Removing them allows the LLM to focus on the actual human-to-assistant dialogue to write a natural, cohesive overview.

            if general_discussion_logs:
                conversation_text = "\n".join(
                    f"{log.speaker_id or 'User'} ({log.role or 'user'}): {log.text}"
                    for log in general_discussion_logs
                )
            else:
                conversation_text = "General discussion was brief or focused entirely on background tasks."

            # Use Groq to summarize and generate action points
            try:
                from groq import AsyncGroq

                from backend.core.config import settings

                if settings.GROQ_API_KEY:
                    client = AsyncGroq(api_key=settings.GROQ_API_KEY)
                    prompt = (
                        f"Please analyze the following conversation history and produce a JSON object with exactly two keys:\n"
                        f'1. "summary": A 1-2 sentence human-like cohesive summary of the session.\n'
                        f'2. "bullets": A list of up to 4 action items/bullet points.\n\n'
                        f"Conversation history:\n{conversation_text}\n\n"
                        f"Return ONLY valid JSON. Example output:\n"
                        f'{{\n  "summary": "The user checked the CRM records and created support tickets for some issues.",\n  "bullets": ["Looked up client details", "Drafted ticket for issue"]\n}}'
                    )
                    resp = await client.chat.completions.create(
                        model="llama-3.1-8b-instant",
                        messages=[
                            {
                                "role": "system",
                                "content": "You are a helpful analyst summarising meeting logs into structured JSON.",
                            },
                            {"role": "user", "content": prompt},
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.3,
                    )
                    ai_data = json.loads(resp.choices[0].message.content.strip())
                    s.summary = ai_data.get("summary")
                    s.bullets = json.dumps(ai_data.get("bullets", []))
            except Exception:
                # Fallback: simple text-based summary
                s.summary = f"Session completed with {len(logs)} transcribed lines."
                s.bullets = json.dumps(["Conversation finalized successfully."])

            # Append background task details fully without summarization
            if bg_data_blocks:
                s.summary = (
                    (s.summary or "")
                    + "\n\n### ✦ Background Task Details (Unsummarized):\n"
                    + "\n\n".join(bg_data_blocks)
                )
        else:
            s.summary = "No conversation occurred in this session."
            s.bullets = json.dumps([])

        await db.commit()

    from backend.core.session_manager import session_manager

    session_manager.remove(session_id)
    return {"status": "ended"}


class DraftEmailReq(BaseModel):
    session_id: str
    from_email: str
    to_email: str
    cc_bcc: Optional[str] = None
    subject: str
    body: str


@router.post("/draft-email")
async def draft_email_route(req: DraftEmailReq):
    from backend.core.session_state import get_state
    from backend.queues.bus import bus
    from backend.tools.system_tasks import _call_text_llm

    prompt = (
        f"You are a premium executive communications assistant. Draft a polished, professional email based on these parameters:\n\n"
        f"── INPUT DATA ──\n"
        f"Sender (From): {req.from_email}\n"
        f"Recipient (To): {req.to_email}\n"
        f"Cc/Bcc: {req.cc_bcc or 'None'}\n"
        f"Proposed Subject: {req.subject}\n"
        f"Writing Instructions / Core Message: {req.body}\n\n"
        f"── WRITING GUIDELINES ──\n"
        f"1. Tone: Exceptionally professional, clear, and business-appropriate.\n"
        f"2. Structure: Maintain paragraphs logically. Expand brief bullet points or phrases in the writing instructions into elegant, cohesive prose.\n"
        f"3. Output Format: Output ONLY the actual email message body itself. Do NOT include email headers (such as To, From, Subject, Cc, Bcc) or dividing lines in your output. Start directly with the salutation and end with the sign-off.\n\n"
        f"── TARGET TEMPLATE (OUTPUT EXACTLY AS SHOWN) ──\n"
        f"Dear Recipient,\n\n"
        f"[Write the body of the formal email here, following the instructions and tone requirements precisely. Do not include placeholders.]\n\n"
        f"Best regards,\n"
        f"{req.from_email}\n"
        f"PILOT Voice OS Assistant"
    )

    draft = await _call_text_llm(
        prompt,
        system_prompt="You are a professional email writing assistant. Draft structured, clean emails with Email, Cc/Bcc, Subject, Main Section Start headers, and Body.",
    )

    # Store in session state
    state = get_state(req.session_id)
    state.pending_email_draft = draft
    state.pending_email_subject = req.subject
    state.pending_email_recipient_email = req.to_email
    state.pending_email_recipient_name = req.to_email.split("@")[0].title()
    state.pending_email_cc_bcc = req.cc_bcc or ""

    # Push tool_start and tool_end events to trigger frontend reactivity
    job_id = str(uuid.uuid4())[:8]
    await bus.emit_event(
        "tool_start",
        {"job_id": job_id, "tool": "write_email", "speaker": "User", "role": "user"},
        req.session_id,
    )

    await bus.emit_event(
        "tool_end",
        {
            "job_id": job_id,
            "tool": "write_email",
            "result": {
                "status": "ok",
                "email_draft": draft,
                "recipient_name": state.pending_email_recipient_name,
                "recipient_email": req.to_email,
                "cc_bcc": state.pending_email_cc_bcc,
                "spoken_reply": "Email drafted successfully from form inputs.",
            },
        },
        req.session_id,
    )

    return {"status": "ok", "email_draft": draft}


class CompileNotesReq(BaseModel):
    recipient_email: Optional[str] = None
    recipient_name: Optional[str] = None


@router.post("/{session_id}/compile-notes")
async def compile_notes(session_id: str, req: CompileNotesReq):
    from backend.tools.meeting_summarizer import compile_meeting_minutes

    args = {"recipient_email": req.recipient_email, "recipient_name": req.recipient_name}
    return await compile_meeting_minutes(args, session_id)
