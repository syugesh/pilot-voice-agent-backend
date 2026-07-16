"""
Front LLM Worker — routes transcript → TTS + optional tool delegation.
Transitions: LISTENING → SPEAKING → LISTENING
              LISTENING → DELEGATING → LISTENING
"""

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from typing import Optional

from backend.queues.bus import QueueBus, TranscriptSpan

logger = logging.getLogger("pilot.front_llm")


@dataclass
class RouteDecision:
    action: str
    preamble: Optional[str]
    tool: Optional[str]
    args: dict
    mode: str
    speaker_id: Optional[str]
    role: Optional[str]
    session_id: str


class FrontLLMWorker:
    def __init__(self, bus: QueueBus):
        self.bus = bus

    def _load(self):
        from backend.services.front_llm import front_llm_provider

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
        from backend.core.session_manager import SessionState, session_manager
        from backend.core.session_state import get_state
        from backend.services.front_llm import front_llm_provider

        ctx = get_state(span.session_id).get_context(6)
        raw = await front_llm_provider.classify(
            text=span.text,
            speaker_id=span.speaker_id or "You",
            role=span.role or "user",
            context=ctx,
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

        logger.info(
            f"[{span.session_id[:6]}] {decision.action} preamble={decision.preamble!r} tool={decision.tool}"
        )

        if decision.action == "ignore":
            return

        if decision.preamble:
            # → SPEAKING
            await session_manager.transition(span.session_id, SessionState.SPEAKING)
            # Show PILOT reply in transcript immediately
            await self.bus.emit_event(
                "transcript",
                {
                    "text": decision.preamble,
                    "speaker": "PILOT",
                    "role": "assistant",
                    "confidence": 1.0,
                    "timestamp": time.time(),
                },
                span.session_id,
            )

            # Cancel any existing active TTS task cleanly
            # if the virtual assistant is currently in the middle of speaking ans the user speaks again,we need to stop the old audio immediately. it checks if there is an active running task and if it not yet completed it calls cancel on the asyncio task. this interrupts the background generator , instantly stopping the old audio stream.

            # Presenter asks: "Tell me about the Q3 targets."
            # The assistant begins reading a long response: "Our Q3 targets are focused on three core areas. First, expanding micro-frontend architecture... Second, improving voice biometrics latency..."
            # After hearing the first sentence, the presenter realizes they only need details on biometrics, so they interrupt: "Wait, tell me only about the voice biometrics part!"

            # The assistant stops reading the old content and immediately begins reading the new, more specific response.

            state = get_state(span.session_id)
            if state.active_tts_task and not state.active_tts_task.done():
                state.active_tts_task.cancel()
                logger.info(f"[{span.session_id[:6]}] Cancelled active TTS task to speak new content.")

            # Synthesise and send audio (async — doesn't block)
            # for preamble until background task is running. non blocking execution.
            task = asyncio.create_task(_speak(decision.preamble, span.session_id))
            state.active_tts_task = task

        if decision.action == "delegate" and decision.tool:
            # → DELEGATING
            await session_manager.transition(span.session_id, SessionState.DELEGATING)
            asyncio.create_task(_delegate(decision))


async def _speak(text: str, session_id: str):
    from backend.core.session_manager import SessionState, session_manager
    from backend.core.session_state import get_state
    from backend.queues.bus import bus
    from backend.services.tts import tts_to_bytes

    current_task = asyncio.current_task()
    logger.info(f"TTS synthesising: '{text}'")
    try:
        state = get_state(session_id)
        # Keep tts_playing False during compilation so late mic frames do not trigger accidental self-interruption
        state.tts_playing = False
        state.barge_in = False

        data, mime = await tts_to_bytes(text, speed=1.1)
        # media type , Multipurpose Internet Mail Extensions.

        # Check if the user interrupted while TTS was compiling
        if state.barge_in:
            logger.info(
                f"[{session_id[:6]}] Interruption detected during TTS compilation. Aborting preamble playback."
            )
            return

        # If a newer TTS task was started, abort gracefully
        if state.active_tts_task is not current_task:
            logger.info(
                f"[{session_id[:6]}] New TTS task started during compilation. Aborting old preamble playback."
            )
            return

        if not data:
            logger.warning("TTS returned empty — no audio sent")
            return

        b64 = base64.b64encode(data).decode("ascii")
        # Set tts_playing to True right before emitting the audio to user's browser
        state.tts_playing = True
        state.tts_start_time = time.time()
        state.barge_in = False

        await bus.emit_event("tts_audio", {"b64": b64, "mime": mime}, session_id)
        logger.info(f"TTS sent {len(data)} bytes ({mime})")

        # Give the audio a moment to play out before clearing tts_playing
        # Calculated based on standard byte length / sample rate playback time (with a buffer)
        playback_duration = max(0.5, len(data) / 48000.0)
        await asyncio.sleep(playback_duration)

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
    # "ppt_clear_presentation": "clear presentation",  # DISABLED: tool removed upstream
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
    "resolution_assess": {"query": (str, 500)},
    "escalate_ticket":   {"synopsis": (str, 300), "category": (str, 50), "symptoms": (str, 500),
                          "priority": (str, 20), "escalation_target": (str, 40)},
    "crm_lookup":        {"query": (str, 150)},
    "general_qa":        {"query": (str, 400)},
    "ppt_navigate":      {"direction": (str, 10)},
    "ppt_jump_to_title": {"query": (str, 200), "slide_number": (int, None)},
    "ppt_summarize":     {},
    "ppt_delete_slide":  {"slide_number": (int, None)},
    # "ppt_clear_presentation": {},  # DISABLED: tool removed upstream
    # Free-form natural-language edit ("make the title bold and add a bullet
    # about Q3 revenue") — the kind-aware editor in ppt_copilot.py parses
    # `instruction` itself rather than expecting pre-structured fields.
    "ppt_edit_slide": {"instruction": (str, 500), "slide_number": (int, None)},
    "ppt_generate_notes": {"slide_number": (int, None), "all": (bool, None)},
    "ppt_last_action": {},
    "ppt_add_slide": {"instruction": (str, 300)},
    "ppt_reorder_slide": {"instruction": (str, 200)},
    # "ppt_improvise_slide": {"prompt": (str, 500)},  # DISABLED: tool removed upstream
    # "ppt_save_slide": {},  # DISABLED: tool removed upstream
    # "route_page": {"page": (str, 30)},  # DISABLED: superseded by navigate_page below
    "navigate_page": {"page": (str, 20)},
}

_ALLOWED_DIRECTION = {"next", "prev", "first", "last"}
_ALLOWED_PAGES = {"dashboard", "ppt", "care", "guidelines", "about", "profile", "settings"}

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


_TOOL_ALIAS_TO_SERVICE_TYPE = {"hotel_search": "hotels", "train_search": "trains", "cab_search": "cabs"}


def _normalize_hallucinated_tool_name(decision: RouteDecision):
    """The classifier's own SYSTEM_PROMPT and _KEYWORDS only ever use
    "flight_search(service_type=...)" — there's no real "hotel_search"/
    "train_search"/"cab_search"/"hotel_book"/etc. tool in TOOL_REGISTRY —
    but Ollama occasionally hallucinates one of those more "obvious-sounding"
    names anyway despite the correct instructions. Nothing downstream
    validates the tool name before it reaches bg_supervisor (policy_gate
    only checks permission, not whether the tool exists at all), so an
    unrecognized name silently fails with "Unknown: hotel_search" — heard
    by the user as "hotel_search isn't recognized" right after PILOT already
    said it would help. Correct it here, the same way the (unused/dead)
    agentic orchestrator.py already does for its own separate path."""
    if decision.tool in _TOOL_ALIAS_TO_SERVICE_TYPE:
        decision.args.setdefault("service_type", _TOOL_ALIAS_TO_SERVICE_TYPE[decision.tool])
        decision.tool = "flight_search"
    elif decision.tool in ("hotel_book", "train_book", "cab_book"):
        decision.tool = "flight_book"


async def _delegate(decision: RouteDecision):
    from backend.core.session_manager import SessionState, session_manager
    from backend.tools.policy import policy_gate

    _normalize_hallucinated_tool_name(decision)

    allowed = await policy_gate.check(
        tool=decision.tool,
        speaker_id=decision.speaker_id,
        role=decision.role,
        session_id=decision.session_id,
    )
    if not allowed:
        import time

        from backend.core.session_state import get_state
        from backend.queues.bus import bus

        denied_msg = "You don't have permission to do this."
        # Transition session to SPEAKING to deliver the voice alert
        await session_manager.transition(decision.session_id, SessionState.SPEAKING)

        # Emit transcript event so it renders in the chat UI
        await bus.emit_event(
            "transcript",
            {
                "text": denied_msg,
                "speaker": "PILOT",
                "role": "assistant",
                "confidence": 1.0,
                "timestamp": time.time(),
            },
            decision.session_id,
        )

        # Cancel any active TTS speech and trigger new vocalization
        state = get_state(decision.session_id)
        if state.active_tts_task and not state.active_tts_task.done():
            state.active_tts_task.cancel()

        task = asyncio.create_task(_speak(denied_msg, decision.session_id))
        state.active_tts_task = task
        return

    # DISABLED: instant fast-path bypass for ppt_clear_presentation/ppt_delete_slide.
    # ppt_clear_presentation no longer exists upstream, and upstream routes every
    # tool — including ppt_delete_slide — through bg_supervisor uniformly rather
    # than special-casing an instant-execution path here, so this block now falls
    # through to the normal job-queue submission below like everything else.
    # if decision.tool in ("ppt_clear_presentation", "ppt_delete_slide"):
    #     from backend.tools.registry import TOOL_REGISTRY
    #     from backend.core.session_state import get_state
    #     from backend.queues.bus import bus
    #     import time
    #
    #     logger.info(f"⚡ [FRONT LLM DELEGATE] Executing {decision.tool} instantly...")
    #     handler = TOOL_REGISTRY.get(decision.tool)
    #     result = await handler(decision.args, decision.session_id)
    #
    #     # Build immediate speech reply
    #     reply = result.get("spoken_reply")
    #     if not reply:
    #         if decision.tool == "ppt_delete_slide":
    #             reply = "Current slide deleted."
    #         else:
    #             reply = "Presentation slides cleared."
    #
    #     # Transition session to SPEAKING to deliver the voice response
    #     await session_manager.transition(decision.session_id, SessionState.SPEAKING)
    #
    #     # Emit transcript event so it renders in the chat UI
    #     await bus.emit_event(
    #         "transcript",
    #         {
    #             "text": reply,
    #             "speaker": "PILOT",
    #             "role": "assistant",
    #             "confidence": 1.0,
    #             "timestamp": time.time(),
    #         },
    #         decision.session_id,
    #     )
    #
    #     state = get_state(decision.session_id)
    #     if state.active_tts_task and not state.active_tts_task.done():
    #         state.active_tts_task.cancel()
    #
    #     task = asyncio.create_task(_speak(reply, decision.session_id))
    #     state.active_tts_task = task
    #     return

    import uuid

    from backend.core.bg_supervisor import Job, bg_supervisor

    job = Job(
        job_id=str(uuid.uuid4())[:8],
        tool=decision.tool,
        args=decision.args,
        mode=decision.mode,
        speaker_id=decision.speaker_id,
        role=decision.role,
        session_id=decision.session_id,
    )
    await bg_supervisor.submit(job)
    from backend.queues.bus import bus

    await bus.emit_event(
        "job_queued",
        {
            "job_id": job.job_id,
            "tool": job.tool,
            "requester": job.speaker_id,
            "mode": job.mode,
        },
        job.session_id,
    )
    # DELEGATING → LISTENING when job completes (bg_supervisor handles this)
