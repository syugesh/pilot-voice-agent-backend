"""
ASR Worker — Whisper transcription + Smart Turn end-of-turn detection.

Pipeline:
  LabeledTurn (PCM) → Whisper → Smart Turn check → transcript_q / Front LLM

Smart Turn may buffer up to MAX_BUFFER_TURNS consecutive incomplete turns per session
before force-flushing. A 2.5-second silence after the last buffered segment also
triggers a flush, so the system never hangs waiting for a turn that never completes.
"""
import asyncio, logging, time
from queues.bus import QueueBus, LabeledTurn, TranscriptSpan

logger = logging.getLogger("pilot.asr")

WAKE_WORDS = [
    "hey pilot",    "hi pilot",    "ok pilot",    "okay pilot",  "yo pilot",
    "hey jarvis",   "jarvis",
    "hey copilot",
    "pilot activate", "pilot help", "pilot listen",
    "hey there",
    "wake up",
    # NOTE: bare "pilot" / "copilot" were removed — the app itself has a page
    # named "PPT Copilot", so a bare single-word match false-triggered on any
    # sentence that mentioned the product's own name (e.g. "open PPT copilot"),
    # discarding everything before it and leaving only a meaningless remainder.
]
WAKE_WORDS_SORTED = sorted(WAKE_WORDS, key=len, reverse=True)

_activation: dict[str, float] = {}

# ── Smart Turn buffering knobs ────────────────────────────────────────────────
MAX_BUFFER_TURNS  = 3      # force-flush after this many buffered incomplete turns
FLUSH_TIMEOUT_S   = 2.5   # force-flush if no new turn arrives within this many seconds
MAX_BUFFER_CHARS  = 200   # force-flush if combined buffered text exceeds this length


def _check_wake(text: str) -> tuple[bool, str]:
    """
    A wake phrase only counts if it's spoken as a lead-in within the first few
    words of the utterance ("Hey Pilot, open the dashboard"). The previous
    implementation did a bare substring search over the whole sentence, which
    matched "pilot"/"copilot" anywhere they appeared — including inside the
    app's own vocabulary ("PPT Copilot", "co-pilot"). That silently discarded
    everything before the match and routed only the trailing fragment (e.g.
    "open PPT copilot" -> remainder "") as the actual command, so sentences
    that legitimately mention the product name never reached the classifier
    intact.
    """
    import re as _re
    orig_words = text.split()
    # Strip punctuation from each word individually (not just the string's
    # ends) — "hey pilot, take me..." must still match "hey pilot" even though
    # Whisper attaches the comma to the token.
    lower_words = [_re.sub(r'^\W+|\W+$', '', w.lower()) for w in orig_words]
    lead_in = lower_words[:4]
    for w in WAKE_WORDS_SORTED:
        w_words = w.split()
        n = len(w_words)
        for start in range(0, max(0, len(lead_in) - n + 1)):
            if lead_in[start:start + n] == w_words:
                remainder = " ".join(orig_words[start + n:]).strip(" ,.-!")
                return True, remainder
    return False, text


class ASRWorker:
    def __init__(self, bus: QueueBus):
        self.bus   = bus
        self._loop: asyncio.AbstractEventLoop | None = None

        # Per-session turn buffer for Smart Turn
        # { session_id: {"texts": [...], "pcms": [...], "turn": LabeledTurn, "ts": float} }
        self._buf:          dict[str, dict] = {}
        self._flush_tasks:  dict[str, asyncio.Task] = {}

    def _load(self):
        from services.stt import whisper_provider
        whisper_provider.load()
        # Pre-load Smart Turn so first real call doesn't stall
        from services.smart_turn import smart_turn
        smart_turn.load()

    async def run(self):
        self._loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._load)
        logger.info("ASR worker started (Whisper + Smart Turn)")
        while True:
            turn: LabeledTurn = await self.bus.labeled_turn_q.get()
            try:
                await self._process(turn)
            except Exception as e:
                logger.error(f"ASR error: {e}", exc_info=True)

    # ── Core processing ───────────────────────────────────────────────────────

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
                text = "hey pilot"
            else:
                text = remainder
        else:
            _activation[turn.session_id] = now

        # ── Smart Turn check ──────────────────────────────────────────────────
        routed_text = await self._smart_turn_gate(turn, text)
        if routed_text is None:
            # Buffered — waiting for the speaker to finish their sentence
            await session_manager.transition(turn.session_id, SessionState.LISTENING)
            return

        # ── Emit transcript + route to Front LLM ──────────────────────────────
        await self._emit_span(turn, routed_text)

    # ── Smart Turn buffering logic ────────────────────────────────────────────

    async def _smart_turn_gate(self, turn: LabeledTurn, text: str) -> str | None:
        """
        Returns the text to route, or None if it was buffered (incomplete turn).
        Combines buffered text with current text when flushing.
        """
        from services.smart_turn import smart_turn

        sid = turn.session_id

        # Combine with any previously buffered text for this session
        existing = self._buf.get(sid, {}).get("texts", [])
        combined = " ".join(existing + [text]).strip()

        is_complete, conf = smart_turn.check(combined)

        logger.info(
            f"[{sid[:8]}] smart_turn: complete={is_complete} conf={conf:.2f} "
            f"text='{combined[:60]}'"
        )

        # Force-flush conditions (regardless of Smart Turn result)
        buf_count = len(existing)
        force = (
            buf_count >= MAX_BUFFER_TURNS
            or len(combined) >= MAX_BUFFER_CHARS
        )

        if is_complete or force:
            # Cancel any pending timeout flush
            self._cancel_flush_task(sid)
            self._buf.pop(sid, None)
            if force and not is_complete:
                logger.debug(f"[{sid[:8]}] smart_turn force-flush after {buf_count+1} segments")
            return combined

        # Incomplete — buffer this segment, schedule timeout flush
        if sid not in self._buf:
            self._buf[sid] = {"texts": [], "turn": turn, "ts": time.time()}
        self._buf[sid]["texts"].append(text)
        self._buf[sid]["ts"] = time.time()

        self._cancel_flush_task(sid)
        self._flush_tasks[sid] = asyncio.create_task(
            self._timeout_flush(sid, FLUSH_TIMEOUT_S)
        )

        logger.debug(f"[{sid[:8]}] smart_turn buffered ({len(self._buf[sid]['texts'])} segs) — waiting for more")
        return None

    async def _timeout_flush(self, session_id: str, delay: float):
        """Force-flush buffered turns after `delay` seconds of silence."""
        await asyncio.sleep(delay)
        entry = self._buf.pop(session_id, None)
        if not entry or not entry["texts"]:
            return
        combined = " ".join(entry["texts"]).strip()
        if not combined:
            return
        logger.info(f"[{session_id[:8]}] smart_turn timeout flush: '{combined[:60]}'")
        await self._emit_span(entry["turn"], combined)

    def _cancel_flush_task(self, session_id: str):
        task = self._flush_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()

    # ── Span emit ─────────────────────────────────────────────────────────────

    async def _emit_span(self, turn: LabeledTurn, text: str):
        from core.session_manager import session_manager, SessionState
        from core.session_state import get_state

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
        # Live customer-sentiment scoring for the CSR dashboard — only in the
        # customer-care usecase, only for customer (non-PILOT) turns, and
        # fire-and-forget so sentiment inference never delays routing the turn
        # to the Front LLM.
        try:
            state = get_state(turn.session_id)
            if getattr(state, "usecase", "") == "customercare" and (turn.role or "").upper() != "PILOT":
                asyncio.create_task(_score_sentiment(turn.session_id, text, turn.timestamp))
        except Exception as e:
            logger.warning(f"sentiment dispatch skipped: {e}")

        await self.bus.transcript_q.put(span)
        await session_manager.transition(turn.session_id, SessionState.LISTENING)


async def _score_sentiment(session_id: str, text: str, timestamp: float):
    """Analyze one customer turn, store it on the session, and push a
    sentiment_update to the CSR dashboard."""
    from services.sentiment import sentiment_provider
    from core.session_state import get_state
    from queues.bus import bus
    try:
        result = await sentiment_provider.analyze(text)
        result["timestamp"] = timestamp
        get_state(session_id).add_sentiment(result)
        await bus.emit_event("sentiment_update", result, session_id)
    except Exception as e:
        logger.warning(f"sentiment scoring failed: {e}")


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
