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
            # Always tell the frontend to stop playback — the backend TTS task may
            # already be done (audio was sent as one blob) so CancelledError won't fire.
            await self.bus.emit_event("tts_stop", {}, span.session_id)
            logger.info(f"[{span.session_id[:6]}] stop phrase '{span.text.strip()}' — frontend notified")
            return

        state = get_state(span.session_id)
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
            # → DELEGATING
            await session_manager.transition(span.session_id, SessionState.DELEGATING)
            asyncio.create_task(_delegate(decision))


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


# ── Tool arg whitelist — type + max-length per field ─────────────────────────
_ARG_SCHEMA: dict[str, dict[str, tuple]] = {
    "flight_search":     {"origin": (str, 80), "destination": (str, 80), "date": (str, 10)},
    "flight_book":       {"flight_id": (str, 40)},
    "kb_search":         {"query": (str, 300)},
    "ticket_create":     {"synopsis": (str, 300), "category": (str, 50), "symptoms": (str, 500)},
    "ticket_update":     {"ticket_id": (str, 40), "status": (str, 30), "note": (str, 300)},
    "ticket_close":      {"ticket_id": (str, 40), "resolution": (str, 300)},
    "crm_lookup":        {"query": (str, 150)},
    "general_qa":        {"query": (str, 400)},
    "ppt_navigate":      {"direction": (str, 10)},
    "ppt_jump_to_title": {"query": (str, 200), "slide_number": (int, None)},
    "ppt_summarize":     {},
    "ppt_delete_slide":  {"slide_number": (int, None)},
}

_ALLOWED_DIRECTION = {"next", "prev", "first", "last"}

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
        elif typ is str:
            val = str(val)[:maxlen] if maxlen else str(val)
            val = val.strip()
            # Strip any HTML/script tags (prevent stored XSS via transcript)
            import re as _re
            val = _re.sub(r'<[^>]+>', '', val)
            if field == "direction" and val not in _ALLOWED_DIRECTION:
                val = "next"
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
        denial = (
            f"Sorry, you have Level {level} access and cannot {action_label}. "
            f"This action requires admin access."
        )
        # Show denial in transcript and speak it
        from queues.bus import bus
        await bus.emit_event("transcript", {
            "text": denial, "speaker": "PILOT", "role": "assistant",
            "confidence": 1.0, "timestamp": __import__("time").time(),
        }, decision.session_id)
        from core.transcript_log import persist_pilot_reply
        asyncio.create_task(persist_pilot_reply(decision.session_id, denial))
        asyncio.create_task(_speak(denial, decision.session_id))
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
