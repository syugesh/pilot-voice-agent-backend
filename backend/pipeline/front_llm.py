"""
Front LLM Worker — routes transcript → TTS + optional tool delegation.
Transitions: LISTENING → SPEAKING → LISTENING
              LISTENING → DELEGATING → LISTENING
"""
import asyncio, logging, base64, time
from dataclasses import dataclass
from typing import Optional
from queues.bus import QueueBus, TranscriptSpan

logger = logging.getLogger("pilot.front_llm")

# ── Stop phrases — checked before Ollama, cancel TTS + BG tasks instantly ────
_STOP_PHRASES = {
    "stop", "stop it", "stop now", "stop talking", "stop task", "stop the task",
    "cancel", "cancel it", "cancel task", "cancel the task", "cancel that",
    "quiet", "be quiet", "go quiet",
    "enough", "that's enough", "thats enough",
    "shut up", "silence",
    "never mind", "nevermind", "forget it", "forget that",
}

def _is_stop_phrase(text: str) -> bool:
    return text.strip().lower().rstrip(".,!?") in _STOP_PHRASES


import re as _re
# Word-boundary matches, not exact-string — real speech carries filler words
# ("alright, yes, confirm that") and ASR punctuation is inconsistent, so an
# exact-match set (the first version of this) missed almost every natural
# phrasing. Deny words are checked first: "no, don't confirm" must not read
# as an affirmative just because "confirm" appears in it.
_CONFIRM_WORD_RE = _re.compile(r'\b(confirm(ed)?|yes|go ahead|do it|proceed|submit)\b', _re.I)
_DENY_WORD_RE = _re.compile(r'\b(no|cancel|don\'?t|stop|wait)\b', _re.I)

def _confirm_intent(text: str) -> Optional[bool]:
    """True = affirmative, False = explicit deny, None = not a confirmation
    reply at all (so an unrelated utterance can't accidentally resolve a
    pending confirmation just because some word loosely overlaps)."""
    t = text.strip()
    if not t:
        return None
    if _DENY_WORD_RE.search(t):
        return False
    if _CONFIRM_WORD_RE.search(t):
        return True
    return None


@dataclass
class RouteDecision:
    action:     str
    preamble:   Optional[str]
    tool:       Optional[str]
    args:       dict
    mode:       str
    speaker_id: Optional[str]
    role:       Optional[str]
    session_id: str


class FrontLLMWorker:
    def __init__(self, bus: QueueBus):
        self.bus = bus

    def _load(self):
        from services.front_llm import front_llm_provider
        front_llm_provider.load()

    async def run(self):
        await asyncio.to_thread(self._load)
        logger.info("FrontLLM worker started")
        while True:
            span: TranscriptSpan = await self.bus.transcript_q.get()
            try:
                await self._process(span)
            except Exception as e:
                logger.error(f"FrontLLM error: {e}", exc_info=True)

    async def _process(self, span: TranscriptSpan):
        from services.front_llm import front_llm_provider
        from core.session_state import get_state
        from core.session_manager import session_manager, SessionState

        # ── Stop-word gate — cancel TTS immediately, no Ollama needed ────────
        if _is_stop_phrase(span.text):
            from core.cancel_tokens import cancel_tts, cancel_all_bg
            cancel_tts()
            cancel_all_bg()
            # A pending "what should the new slide be about?" question must
            # not silently hijack whatever the user says next once they've
            # explicitly backed out of it.
            get_state(span.session_id).pending_add_slide = None
            # Always tell the frontend to stop playback — the backend TTS task may
            # already be done (audio was sent as one blob) so CancelledError won't fire.
            await self.bus.emit_event("tts_stop", {}, span.session_id)
            logger.info(f"[{span.session_id[:6]}] stop phrase '{span.text.strip()}' — frontend notified")
            return

        state = get_state(span.session_id)

        # ── Pending destructive-tool confirmation gate ────────────────────
        # Checked BEFORE LLM classification, on the raw utterance, so this
        # can never be confused for an unrelated tool call and so a
        # same-turn spoof attempt from a different speaker is rejected
        # deterministically rather than by asking an LLM to judge intent.
        pc = state.pending_confirm
        if pc and not pc.get("resolved"):
            intent = _confirm_intent(span.text)
            if intent is not None:
                same_speaker = bool(span.speaker_id) and span.speaker_id == pc.get("speaker_id")
                role_ok = (span.role or "").lower() in ("csr", "manager", "admin")
                if intent is True and same_speaker and role_ok:
                    pc["resolved"] = True
                    logger.info(f"[{span.session_id[:6]}] confirm accepted tool={pc['tool']} "
                                f"speaker={span.speaker_id}")
                else:
                    # Wrong speaker, wrong role, or an explicit "no" — do NOT
                    # resolve as confirmed. Wake the waiter now (rather than
                    # letting it time out) so the UI reflects the block
                    # immediately, but PolicyGate._confirm() will see
                    # resolved=False and treat this as identity_mismatch.
                    logger.warning(
                        f"[{span.session_id[:6]}] confirm rejected tool={pc['tool']} "
                        f"attempted_by={span.speaker_id} role={span.role} "
                        f"reason={'explicit_deny' if intent is False else 'identity_mismatch'}"
                    )
                pc["event"].set()
                return

        ctx = state.get_context(6)
        usecase = getattr(state, "usecase", None) or \
                  getattr(session_manager.get(span.session_id), "usecase", "general") or "general"
        raw = await front_llm_provider.classify(
            text=span.text,
            speaker_id=span.speaker_id or "You",
            role=span.role or "user",
            context=ctx,
            usecase=usecase,
            session_id=span.session_id,
        )

        decision = RouteDecision(
            action=raw.get("action", "respond_now"),
            preamble=raw.get("preamble"),
            tool=raw.get("tool"),
            args=raw.get("args", {}),
            mode=raw.get("mode", "queue"),
            speaker_id=span.speaker_id,
            role=span.role,
            session_id=span.session_id,
        )

        logger.info(f"[{span.session_id[:6]}] {decision.action} "
                    f"preamble={decision.preamble!r} tool={decision.tool}")

        if decision.action == "ignore":
            return

        if decision.preamble:
            # → SPEAKING
            await session_manager.transition(span.session_id, SessionState.SPEAKING)
            await self.bus.emit_event("transcript", {
                "text":       decision.preamble,
                "speaker":    "PILOT",
                "role":       "assistant",
                "confidence": 1.0,
                "timestamp":  time.time(),
            }, span.session_id)
            from core.transcript_log import persist_pilot_reply
            asyncio.create_task(persist_pilot_reply(span.session_id, decision.preamble))
            # Register task so stop words can cancel it mid-speech
            from core.cancel_tokens import register_tts
            tts_task = asyncio.create_task(_speak(decision.preamble, span.session_id))
            register_tts(tts_task)

        if decision.action == "delegate" and decision.tool:
            # → DELEGATING (single-tool fast path — unchanged)
            await session_manager.transition(span.session_id, SessionState.DELEGATING)
            asyncio.create_task(_delegate(decision))

        if decision.action == "agent":
            # → autonomous ReAct worker. Front LLM hands it a GOAL; the worker
            # reasons/acts/observes and returns a result the gateway phrases.
            await session_manager.transition(span.session_id, SessionState.DELEGATING)
            asyncio.create_task(_run_agent(decision, usecase))


# ── Output gateway ───────────────────────────────────────────────────────────
# Every worker/agent result funnels back through here — the Front LLM is the
# single output surface. The SINK depends on usecase: spoken (TTS) in
# ppt/general, silent-to-dashboard in customercare (where PILOT observes a live
# call and must never talk). The worker never emits to the user directly.

async def gateway_emit(text: str, session_id: str, usecase: str):
    from queues.bus import bus
    from core.session_manager import session_manager, SessionState
    from core.transcript_log import persist_pilot_reply

    text = (text or "").strip()
    if not text:
        await session_manager.transition(session_id, SessionState.LISTENING)
        return

    if usecase == "customercare":
        # Silent sink: surface the agent's conclusion on the dashboard only.
        await bus.emit_event("agent_note", {"text": text}, session_id)
        await session_manager.transition(session_id, SessionState.LISTENING)
        return

    # Spoken sink (ppt / general): show in transcript + TTS.
    await session_manager.transition(session_id, SessionState.SPEAKING)
    await bus.emit_event("transcript", {
        "text": text, "speaker": "PILOT", "role": "assistant",
        "confidence": 1.0, "timestamp": time.time(),
    }, session_id)
    asyncio.create_task(persist_pilot_reply(session_id, text))
    from core.cancel_tokens import register_tts
    register_tts(asyncio.create_task(_speak(text, session_id)))


async def _run_agent(decision: "RouteDecision", usecase: str):
    from services.react_agent import run as run_react
    from tools.policy import ROLE_PERMS

    role = (decision.role or "user").lower()
    perms = ROLE_PERMS.get(role, ROLE_PERMS["user"])
    # The worker may only reach tools this speaker's role can run (the loop
    # re-checks each call via policy_gate too — this just trims its menu).
    allowed = sorted(perms) if "*" not in perms else sorted(
        {t for p in ROLE_PERMS.values() for t in p if t != "*"})

    goal = (decision.args or {}).get("goal") or ""
    result = await run_react(goal, session_id=decision.session_id,
                             speaker_id=decision.speaker_id, role=decision.role,
                             allowed_tools=allowed)
    await gateway_emit(result.get("final", ""), decision.session_id, usecase)


async def _speak(text: str, session_id: str):
    from services.tts import tts_to_bytes
    from queues.bus import bus
    from core.session_manager import session_manager, SessionState

    logger.info(f"TTS synthesising: '{text}'")
    try:
        data, mime = await tts_to_bytes(text, speed=1.1)
        if not data:
            logger.warning("TTS returned empty — no audio sent")
            await session_manager.transition(session_id, SessionState.LISTENING)
            return
        b64 = base64.b64encode(data).decode("ascii")
        await bus.emit_event("tts_audio", {"b64": b64, "mime": mime}, session_id)
        logger.info(f"TTS sent {len(data)} bytes ({mime})")
        await session_manager.transition(session_id, SessionState.LISTENING)
    except asyncio.CancelledError:
        # Stop word triggered — send a stop signal to the frontend to cut playback
        await bus.emit_event("tts_stop", {}, session_id)
        await session_manager.transition(session_id, SessionState.LISTENING)
        logger.info("TTS cancelled by stop word")
    except Exception as e:
        logger.error(f"TTS error: {e}", exc_info=True)
        await session_manager.transition(session_id, SessionState.LISTENING)


_ROLE_LEVELS = {
    "guest": 1, "customer": 1,
    "user": 1,
    "csr": 2, "operator": 2, "developer": 2,
    "manager": 3,
    "admin": 4,
}

_TOOL_DENIAL = {
    "ppt_delete_slide": "delete slides",
    "ppt_navigate":     "navigate slides",
    "ticket_close":     "close tickets",
    "flight_book":      "book flights",
}


def _min_required_level(tool: str) -> int | None:
    """Lowest access level among roles actually permitted to run `tool`, read
    straight from ROLE_PERMS so the denial message can't drift from policy.
    Returns None if no non-admin role grants it (admin-only via '*')."""
    from tools.policy import ROLE_PERMS
    levels = [
        _ROLE_LEVELS.get(r, 1)
        for r, perms in ROLE_PERMS.items()
        if tool in perms  # explicit grant only; '*' (admin) handled below
    ]
    if levels:
        return min(levels)
    # Only admin's wildcard covers it.
    return _ROLE_LEVELS.get("admin", 4)


# ── Tool arg whitelist — type + max-length per field ─────────────────────────
_ARG_SCHEMA: dict[str, dict[str, tuple]] = {
    # "query" carries the raw utterance through — travel_search does its own
    # service_type detection (flights/hotels/trains/cabs) by scanning it for
    # keywords, so without it every request silently defaults to "flights"
    # regardless of what was actually asked (origin/destination alone don't
    # say whether this is a flight, hotel, or train search).
    "travel_search":     {"origin": (str, 80), "destination": (str, 80), "date": (str, 10), "query": (str, 300)},
    "flight_book":       {"flight_id": (str, 40)},
    "kb_search":         {"query": (str, 300)},
    "ticket_create":     {"synopsis": (str, 300), "category": (str, 50), "symptoms": (str, 500)},
    "ticket_update":     {"ticket_id": (str, 40), "status": (str, 30), "note": (str, 300)},
    "ticket_close":      {"ticket_id": (str, 40), "resolution": (str, 300)},
    "crm_lookup":        {"query": (str, 150)},
    "resolution_assess": {"query": (str, 500)},
    "escalate_ticket":   {"synopsis": (str, 300), "category": (str, 50), "symptoms": (str, 500),
                          "priority": (str, 20), "escalation_target": (str, 40)},
    "general_qa":        {"query": (str, 400)},
    "ppt_navigate":      {"direction": (str, 10)},
    "ppt_jump_to_title": {"query": (str, 200), "slide_number": (int, None)},
    "ppt_summarize":     {},
    "ppt_delete_slide":  {"slide_number": (int, None)},
    "ppt_edit_slide":    {"instruction": (str, 500), "slide_number": (int, None)},
    "ppt_generate_notes": {"slide_number": (int, None), "all": (bool, None)},
    "ppt_last_action":   {},
    "ppt_add_slide":     {"instruction": (str, 300)},
    "navigate_page":     {"page": (str, 20)},
}

_ALLOWED_DIRECTION = {"next", "prev", "first", "last"}
# Must match tools/navigation.py's VALID_PAGES — "guidelines"/"settings"
# aren't real frontend routes (see that file's comment for why).
_ALLOWED_PAGES = {"dashboard", "ppt", "care", "about", "profile"}

def _sanitize_args(tool: str, args: dict) -> dict:
    """Validate and coerce LLM-generated tool args. Drops unknown keys, enforces types/lengths."""
    schema = _ARG_SCHEMA.get(tool)
    if schema is None:
        return {}
    clean: dict = {}
    for field, (typ, maxlen) in schema.items():
        val = args.get(field)
        if val is None:
            continue
        if typ is int:
            try:
                clean[field] = max(0, int(val))
            except (ValueError, TypeError):
                pass
        elif typ is bool:
            clean[field] = bool(val)
        elif typ is str:
            val = str(val)[:maxlen] if maxlen else str(val)
            val = val.strip()
            # Strip any HTML/script tags (prevent stored XSS via transcript)
            import re as _re
            val = _re.sub(r'<[^>]+>', '', val)
            if field == "direction" and val not in _ALLOWED_DIRECTION:
                val = "next"
            if field == "page" and val.lower() not in _ALLOWED_PAGES:
                continue
            if val:
                clean[field] = val
    return clean


async def _delegate(decision: RouteDecision):
    from tools.policy import policy_gate
    from core.session_manager import session_manager, SessionState

    allowed = await policy_gate.check(
        tool=decision.tool, speaker_id=decision.speaker_id,
        role=decision.role, session_id=decision.session_id,
    )
    if not allowed:
        await session_manager.transition(decision.session_id, SessionState.LISTENING)
        role  = (decision.role or "user").lower()
        level = _ROLE_LEVELS.get(role, 1)
        action_label = _TOOL_DENIAL.get(decision.tool, "use that feature")
        # Report the ACTUAL minimum level this tool needs, not a hardcoded
        # "admin" — many tools only need CSR/manager, so "requires admin
        # access" was both wrong and confusing (e.g. an admin turn misheard as
        # a lower role would be told it needs a level it in fact outranks).
        required = _min_required_level(decision.tool)
        req_txt = f"Level {required} access" if required else "a higher access level"
        denial = (
            f"Sorry, you have Level {level} access and cannot {action_label}. "
            f"This action requires {req_txt}."
        )
        # Surface in the transcript/UI, but do NOT speak it — an access denial
        # is operator feedback, not something to read aloud on a call (and in
        # customercare this path is unreachable anyway; the guard remains for
        # the conversational modes).
        from queues.bus import bus
        await bus.emit_event("transcript", {
            "text": denial, "speaker": "PILOT", "role": "assistant",
            "confidence": 1.0, "timestamp": __import__("time").time(),
        }, decision.session_id)
        from core.transcript_log import persist_pilot_reply
        asyncio.create_task(persist_pilot_reply(decision.session_id, denial))
        return

    from core.bg_supervisor import bg_supervisor, Job
    import uuid
    safe_args = _sanitize_args(decision.tool, decision.args)
    job = Job(
        job_id=str(uuid.uuid4())[:8],
        tool=decision.tool, args=safe_args, mode=decision.mode,
        speaker_id=decision.speaker_id, role=decision.role,
        session_id=decision.session_id,
    )
    await bg_supervisor.submit(job)
    from queues.bus import bus
    await bus.emit_event("job_queued", {
        "job_id": job.job_id, "tool": job.tool,
        "requester": job.speaker_id, "mode": job.mode,
    }, job.session_id)
    # DELEGATING → LISTENING when job completes (bg_supervisor handles this)
