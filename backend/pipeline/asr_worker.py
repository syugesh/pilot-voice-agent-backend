"""
ASR Worker — faster-whisper with optional wake word.
The mic button is the activation signal — all speech while the mic is on is processed.
"Hey Pilot" is still recognised (greets + resets clock) but is no longer required.
"""
import asyncio, logging, time
from queues.bus import QueueBus, LabeledTurn, TranscriptSpan

logger = logging.getLogger("pilot.asr")

# ── Wake words ────────────────────────────────────────────────────────────────
# Sorted longest-first so "hey pilot" matches before "pilot" on the same phrase.
WAKE_WORDS = [
    "hey pilot",    "hi pilot",    "ok pilot",    "okay pilot",  "yo pilot",
    "hey jarvis",   "jarvis",
    "hey copilot",  "copilot",
    "pilot activate", "pilot help", "pilot listen",
    "hey there",
    "wake up",
    "pilot",        # broad fallback — last so shorter phrases don't shadow above
]
WAKE_WORDS_SORTED = sorted(WAKE_WORDS, key=len, reverse=True)

# Tracks last-speech time per session (used to extend clock on each turn)
_activation: dict[str, float] = {}


def _check_wake(text: str) -> tuple[bool, str]:
    """Return (found, text_after_wake_word). Checks longest match first."""
    t = text.lower().strip()
    for w in WAKE_WORDS_SORTED:
        if w in t:
            idx = t.find(w) + len(w)
            remainder = text[idx:].strip(" ,.-!")
            return True, remainder
    return False, text


class ASRWorker:
    def __init__(self, bus: QueueBus):
        self.bus = bus
        self._loop: asyncio.AbstractEventLoop | None = None

    def _load(self):
        from services.stt import whisper_provider
        whisper_provider.load()

    async def run(self):
        self._loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._load)
        logger.info("ASR worker started")
        while True:
            turn: LabeledTurn = await self.bus.labeled_turn_q.get()
            try:
                await self._process(turn)
            except Exception as e:
                logger.error(f"ASR error: {e}", exc_info=True)

    async def _process(self, turn: LabeledTurn):
        from services.stt import whisper_provider
        from core.session_manager import session_manager, SessionState
        from core.session_state import get_state

        await session_manager.transition(turn.session_id, SessionState.PROCESSING)

        text = await whisper_provider.transcribe(turn.pcm)
        text = text.strip()
        if not text:
            await session_manager.transition(turn.session_id, SessionState.LISTENING)
            return

        # ── Wake word gate ────────────────────────────────────────────────────
        now = time.time()
        found, remainder = _check_wake(text)

        if found:
            _activation[turn.session_id] = now
            await self.bus.emit_event("wake_word", {
                "heard": text, "remainder": remainder
            }, turn.session_id)
            logger.info(f"[{turn.session_id[:8]}] wake word in '{text}' → cmd='{remainder}'")

            if not remainder:
                # Pure wake word — greet without routing a tool
                text = "hey pilot"   # triggers keyword → "Hey! I'm listening — what can I do?"
            else:
                text = remainder     # process the command that followed the wake word

        else:
            # Mic is on — user is intentionally speaking.
            # Process every command without requiring a wake word; just keep the clock fresh.
            _activation[turn.session_id] = now
        # ─────────────────────────────────────────────────────────────────────

        span = TranscriptSpan(
            text=text, session_id=turn.session_id,
            speaker_id=turn.speaker_id, role=turn.role,
            confidence=turn.confidence, timestamp=turn.timestamp,
        )

        get_state(turn.session_id).add_span({
            "speaker": turn.speaker_id, "role": turn.role,
            "text": text, "confidence": turn.confidence,
        })

        logger.info(f"[{turn.session_id[:8]}] transcript → '{text[:60]}' speaker={turn.speaker_id}")
        await self.bus.emit_event("transcript", {
            "text":       text,
            "speaker":    turn.speaker_id or "You",
            "role":       turn.role or "user",
            "confidence": round(turn.confidence, 3),
            "timestamp":  turn.timestamp,
        }, turn.session_id)

        await _persist(span)
        await self.bus.transcript_q.put(span)
        await session_manager.transition(turn.session_id, SessionState.LISTENING)


async def _persist(span: TranscriptSpan):
    from db.engine import AsyncSessionLocal
    from db.models import TranscriptLog
    try:
        async with AsyncSessionLocal() as db:
            db.add(TranscriptLog(
                session_id=span.session_id, speaker_id=span.speaker_id,
                role=span.role, text=span.text,
                confidence=span.confidence, timestamp=span.timestamp,
            ))
            await db.commit()
    except Exception as e:
        logger.error(f"Persist failed: {e}", exc_info=True)
