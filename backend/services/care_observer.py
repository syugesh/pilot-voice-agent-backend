"""
Care-mode observer — the SILENT background analyst for the customercare use case.

In customercare, PILOT is NOT a conversation participant: the CSR talks to the
customer directly (often on one shared headset/softphone line), and PILOT only
*watches*. It must never speak, never ask clarifying questions, and never route
the rep/customer's conversational speech into voice-assistant tools (which would
otherwise produce spoken replies and spurious RBAC denials on a live call).

This module is what replaces that vocal/routing path for care mode: given the
rolling transcript, it extracts the canonical fields a CSR ticket needs
(synopsis + symptom timeline) and pushes them to the dashboard as a
`care_observe` event. Fire-and-forget, debounced, and entirely output-free on
the audio channel — sentiment scoring (services/sentiment.py) and the on-demand
resolution engine (services/resolution.py) run alongside it, also silently.

Only the CSR's explicit, identity-gated actions (e.g. submitting the ticket)
ever mutate anything durable; the observer is read-only analysis for the screen.
"""
import asyncio, logging, re

logger = logging.getLogger("pilot.care_observer")

# Debounce: coalesce a burst of quick turns into one extraction pass rather
# than re-summarizing on every single utterance (each pass is an LLM call).
_DEBOUNCE_S = 1.2
_timers: dict[str, asyncio.Task] = {}

# Lightweight symptom cues — a deterministic first pass so the timeline shows
# something immediately even before the LLM synopsis returns. The LLM pass
# (below) produces the polished synopsis; these keep the dashboard live.
_SYMPTOM_CUES = re.compile(
    r'\b(light|led|blink|amber|red|flash|reboot|restart|reset|drop|outage|'
    r'slow|disconnect|down|error|blue screen|no signal|dsl|modem|router)\b',
    re.I,
)


def observe(session_id: str, usecase: str) -> None:
    """Schedule (debounced) a silent extraction pass for this session. Safe to
    call on every transcript turn — only the last call in a burst runs."""
    if (usecase or "").lower() != "customercare":
        return
    existing = _timers.get(session_id)
    if existing and not existing.done():
        existing.cancel()
    _timers[session_id] = asyncio.create_task(_debounced(session_id))


async def _debounced(session_id: str) -> None:
    try:
        await asyncio.sleep(_DEBOUNCE_S)
    except asyncio.CancelledError:
        return
    try:
        await _extract_and_emit(session_id)
    except Exception as e:
        logger.warning(f"[{session_id[:8]}] care observe failed: {e}")


async def _extract_and_emit(session_id: str) -> None:
    from core.session_state import get_state
    from services.session_summary import summarize_issue
    from queues.bus import bus

    state = get_state(session_id)
    spans = state.get_context(20)
    # Only the customer/rep conversation — PILOT never contributes turns here,
    # but guard anyway so a stray assistant line can't pollute the synopsis.
    convo = [s for s in spans if (s.get("role") or "").upper() != "PILOT" and (s.get("text") or "").strip()]
    if not convo:
        return

    # Deterministic symptom timeline (instant, no LLM) — customer turns only.
    symptoms: list[str] = []
    for s in convo:
        text = (s.get("text") or "").strip()
        if _SYMPTOM_CUES.search(text):
            symptoms.append(text)

    # LLM synopsis (canonical summary field). summarize_issue already degrades
    # gracefully to a placeholder string if every provider is down.
    synopsis = await summarize_issue(convo)

    await bus.emit_event("care_observe", {
        "synopsis": synopsis,
        "symptom_timeline": symptoms[-6:],   # most recent, capped for the panel
        "turn_count": len(convo),
    }, session_id)
    logger.info(f"[{session_id[:8]}] care observe → synopsis + {len(symptoms)} symptom turn(s)")

    # Autonomous ReAct observer — incremental, not per-tick. The cheap
    # synopsis/timeline above runs every debounce; the (expensive, tool-calling)
    # agent only spawns when there's meaningfully MORE conversation than the
    # last agent run and no agent is already in flight for this session. This is
    # the "debounced incremental ReAct" the CSR architecture calls for — it
    # keeps the dashboard live without re-running a full loop (and re-hitting
    # kb_search/crm/resolution) on every utterance.
    _maybe_run_agent(session_id, convo)


# How many new customer/rep turns must accrue before re-running the agent.
_AGENT_TURN_STRIDE = 4
_last_agent_turns: dict[str, int] = {}
_agent_running: set[str] = set()


def _maybe_run_agent(session_id: str, convo: list[dict]) -> None:
    if session_id in _agent_running:
        return
    n = len(convo)
    if n - _last_agent_turns.get(session_id, 0) < _AGENT_TURN_STRIDE:
        return
    _last_agent_turns[session_id] = n
    _agent_running.add(session_id)
    asyncio.create_task(_run_care_agent(session_id, convo))


async def _run_care_agent(session_id: str, convo: list[dict]) -> None:
    """Run the ReAct worker to keep the CSR dashboard's recommendation current,
    then route its conclusion through the Front LLM gateway's SILENT sink — the
    customer never hears it; only the rep's screen updates."""
    try:
        from services.react_agent import run as run_react
        from pipeline.front_llm import gateway_emit
        issue = " ".join((s.get("text") or "") for s in convo)[:1200]
        goal = (
            "For this live support call, retrieve the most relevant knowledge-base "
            "procedure and assess whether the rep should resolve on the call or "
            "escalate. Summarize the recommendation in one sentence. Issue so far: "
            f"{issue}"
        )
        # Runs as the session's CSR (fallback identity) so policy gating applies;
        # the loop's own policy_gate.check() enforces it per tool call.
        from core.session_state import get_state
        state = get_state(session_id)
        result = await run_react(
            goal, session_id=session_id,
            speaker_id=state.fallback_name or "csr",
            role=state.fallback_role or "csr",
            allowed_tools=["kb_search", "crm_lookup", "resolution_assess"],
        )
        # SILENT sink — customercare gateway routes to the dashboard, no TTS.
        await gateway_emit(result.get("final", ""), session_id, "customercare")
    except Exception as e:
        logger.warning(f"[{session_id[:8]}] care agent failed: {e}")
    finally:
        _agent_running.discard(session_id)


def clear(session_id: str) -> None:
    t = _timers.pop(session_id, None)
    if t and not t.done():
        t.cancel()
    _last_agent_turns.pop(session_id, None)
    _agent_running.discard(session_id)
