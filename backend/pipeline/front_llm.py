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
            # Show PILOT reply in transcript immediately
            await self.bus.emit_event("transcript", {
                "text":       decision.preamble,
                "speaker":    "PILOT",
                "role":       "assistant",
                "confidence": 1.0,
                "timestamp":  time.time(),
            }, span.session_id)
            # Synthesise and send audio (async — doesn't block)
            asyncio.create_task(_speak(decision.preamble, span.session_id))

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
        # → back to LISTENING after speaking
        await session_manager.transition(session_id, SessionState.LISTENING)
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
        asyncio.create_task(_speak(denial, decision.session_id))
        return

    from core.bg_supervisor import bg_supervisor, Job
    import uuid
    job = Job(
        job_id=str(uuid.uuid4())[:8],
        tool=decision.tool, args=decision.args, mode=decision.mode,
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
