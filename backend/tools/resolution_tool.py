"""
resolution_assess tool — the CSR-dashboard orchestrator.

Pulls the live call's transcript + rolling sentiment history off the session,
retrieves the best-matching KB procedures for the current issue, runs the
resolution/escalation engine, generates the issue summary, and emits a single
`resolution_update` WS event the dashboard renders (confidence, recommendation,
reasons, KB citations, summary). Invoked like any other tool (voice "should I
escalate this?" or a dashboard button), so it flows through the normal
bg-supervisor + tool_end path too.
"""
import logging
logger = logging.getLogger("pilot.tools.resolution")


_NON_CUSTOMER_ROLES = {"PILOT", "REP", "AGENT"}


def _customer_transcript(spans: list[dict]) -> str:
    """Join just the customer's turns into the issue text the engine reasons
    over — excludes PILOT (browser-mic sessions, where PILOT is the other
    "speaker") and REP/AGENT (telephony calls, where a real human rep is the
    other party, not PILOT itself — see api/ws_telephony.py)."""
    lines = []
    for s in spans:
        role = (s.get("role") or "").upper()
        if role in _NON_CUSTOMER_ROLES or s.get("speaker") in _NON_CUSTOMER_ROLES:
            continue
        t = (s.get("text") or "").strip()
        if t:
            lines.append(t)
    return " ".join(lines)


async def resolution_assess(args: dict, session_id: str) -> dict:
    from backend.core.session_state import get_state
    from backend.services.kb_index import kb_index
    from backend.services.resolution import assess
    from backend.services.session_summary import summarize_issue
    from backend.queues.bus import bus

    state = get_state(session_id)
    spans = state.get_context(20)
    transcript_text = _customer_transcript(spans) or (args.get("query") or "")
    if not transcript_text.strip():
        return {"spoken_reply": "There's no customer conversation to assess yet."}

    kb_hits = await kb_index.search(transcript_text, k=3)
    result = await assess(transcript_text, state.sentiment_history, kb_hits)
    issue_summary = await summarize_issue(spans)

    payload = {**result, "issue_summary": issue_summary, "kb_articles": kb_hits}
    await bus.emit_event("resolution_update", payload, session_id)

    # A concise spoken line for the rep; the rich detail is on the dashboard.
    if result["recommendation"] == "escalate":
        spoken = f"I'd recommend escalating this to {result['escalation_target']}. {result['reasoning']}"
    else:
        spoken = f"This looks resolvable on the call — confidence {int(result['resolution_confidence']*100)} percent. {result['reasoning']}"

    return {"status": "ok", "spoken_reply": spoken, **payload}


async def escalate_ticket(args: dict, session_id: str) -> dict:
    """Create a support ticket pre-tagged as an escalation from the current
    assessment — the 'Create escalation ticket' dashboard action."""
    from backend.tools.tickets import ticket_create
    synopsis = args.get("synopsis") or "Escalated from live call"
    return await ticket_create({
        "synopsis": synopsis,
        "category": args.get("category", "escalation"),
        "symptoms": args.get("symptoms", ""),
        "priority": args.get("priority", "high"),
        "escalated": True,
        "escalation_target": args.get("escalation_target", "L2 Support"),
    }, session_id)
