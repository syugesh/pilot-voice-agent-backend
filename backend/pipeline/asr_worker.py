"""
ASR Worker — faster-whisper.
Transitions session: LISTENING → PROCESSING → LISTENING
"""

import asyncio
import logging
import re
import time

from backend.queues.bus import LabeledTurn, QueueBus, TranscriptSpan

logger = logging.getLogger("pilot.asr")


class ASRWorker:
    def __init__(self, bus: QueueBus):
        self.bus = bus

    def _load(self):
        from backend.services.stt import whisper_provider

        whisper_provider.load()

    async def run(self):
        await asyncio.to_thread(self._load)
        logger.info("ASR worker started")
        while True:
            turn: LabeledTurn = await self.bus.labeled_turn_q.get()
            try:
                await self._process(turn)
            except Exception as e:
                logger.error(f"ASR error: {e}", exc_info=True)

    async def _process(self, turn: LabeledTurn):
        from backend.core.session_manager import SessionState, session_manager
        from backend.core.session_state import get_state
        from backend.services.stt import whisper_provider

        # → PROCESSING
        await session_manager.transition(turn.session_id, SessionState.PROCESSING)

        # SmartTurn's fallback-heuristic pass already transcribed this exact
        # audio while deciding the turn was complete — reuse it instead of
        # paying for a second full Whisper pass over the same bytes.
        if turn.cached_text is not None:
            text = turn.cached_text
        else:
            text = await whisper_provider.transcribe(turn.pcm)
        if not text.strip():
            await session_manager.transition(turn.session_id, SessionState.LISTENING)
            return

        # ── PRINT WHAT IS HEARD DIRECTLY TO TERMINAL ──
        # print(f'\n🎙️ [PILOT LISTENS] Speaker: {turn.speaker_id or "unknown"} | Text: "{text}"\n')

        # Clean punctuation from the whole string to make wake word matching robust (e.g. "Hey, Pilot" -> "hey pilot")
        normalized_text = re.sub(r"[^\w\s]", "", text.lower()).strip()
        state = get_state(turn.session_id)

        # ── WAKE WORD & SLEEP WORD HANDLING ──
        # `\s*` (not a literal space) between the address word and "pilot"/
        # "violet" — Whisper sometimes glues them into one word ("Okpilot",
        # "Heypilot"), which a plain substring check on "ok pilot" would
        # silently fail to match, dropping the whole utterance with no
        # wake word ever having "worked" from the user's point of view.
        is_wake_word = any(
            re.search(rf"\b{w}\s*(?:pilot|violet)\b", normalized_text)
            for w in [
                "hey", "hello", "hi", "wake up", "their", "there", "the",
                "ok", "okay", "a", "ey", "ah",
            ]
        )
        is_sleep_word = any(
            s in normalized_text for s in ["stop listening", "go to sleep", "sleep pilot", "mute pilot"]
        )

        # Meeting sessions (Talkinia — see ws_audio.py's "meeting_" session_id
        # detection) are multiple people conversing WITH EACH OTHER, not with
        # PILOT — no wake word needed and no commands recognized at all.
        # Every sentence is just captured (diarized, shown live, kept in the
        # ring buffer) for the eventual summary; nothing is ever routed to
        # the Front LLM, so PILOT never tries to respond mid-meeting.
        if state.usecase == "meeting":
            await self._transcribe_only(turn, text)
            await session_manager.transition(turn.session_id, SessionState.LISTENING)
            return

        if is_sleep_word:
            state.ambient_listening_active = False
            logger.info(
                f"[{turn.session_id[:6]}] Sleep word received! Pinned state: ambient_listening_active=False"
            )
            await self.bus.emit_event(
                "transcript",
                {
                    "text": "Go to sleep. Ambient listening is paused. Say 'Hey Pilot' to wake me up.",
                    "speaker": "PILOT",
                    "role": "assistant",
                    "confidence": 1.0,
                    "timestamp": time.time(),
                },
                turn.session_id,
            )
            await session_manager.transition(turn.session_id, SessionState.LISTENING)
            return

        if not state.ambient_listening_active:
            # Check if this is a direct slide control navigation command.
            # Slide navigation clicks should be allowed to bypass the ambient sleep state
            # for standard presentation slide-deck control convenience.
            is_slide_nav = any(
                cmd in normalized_text
                for cmd in [
                    "next slide",
                    "previous slide",
                    "go back",
                    "slide back",
                    "last slide",
                    "first slide",
                    "jump to slide",
                    "go to slide",
                    "slide number",
                ]
            )

            if is_wake_word or is_slide_nav:
                if is_wake_word:
                    state.ambient_listening_active = True
                    logger.info(
                        f"[{turn.session_id[:6]}] Wake word matching verified! Pinned state: ambient_listening_active=True"
                    )
                    await self.bus.emit_event(
                        "transcript",
                        {
                            "text": "Verified. I am online and listening for your commands.",
                            "speaker": "PILOT",
                            "role": "assistant",
                            "confidence": 1.0,
                            "timestamp": time.time(),
                        },
                        turn.session_id,
                    )
                    # Cut the trigger wake-phrase out so it doesn't execute accidental tasks.
                    # \s* (not \s+) so a glued "Okpilot" strips cleanly too, matching the
                    # tolerant is_wake_word check above.
                    text = re.sub(
                        r"\b(hey|hello|hi|wake up|their|there|the|ok|okay|a|ey|ah)\s*,?\s*(?:pilot|violet)\b",
                        "",
                        text,
                        flags=re.IGNORECASE,
                    ).strip()
                    if not text:
                        await session_manager.transition(turn.session_id, SessionState.LISTENING)
                        return
                else:
                    # Let it pass through silently for slide navigation control
                    logger.info(
                        f"[{turn.session_id[:6]}] Bypassing ambient sleep gate for slide navigation control: '{text}'"
                    )
            else:
                # Gated state: Discard speech transcript silently
                logger.debug(f"[{turn.session_id[:6]}] Discarded transcript: '{text}' — Wake word required.")
                await session_manager.transition(turn.session_id, SessionState.LISTENING)
                return

        span = TranscriptSpan(
            text=text,
            session_id=turn.session_id,
            speaker_id=turn.speaker_id,
            role=turn.role,
            confidence=turn.confidence,
            timestamp=turn.timestamp,
        )

        # Update ring buffer
        get_state(turn.session_id).add_span(
            {"speaker": turn.speaker_id, "role": turn.role, "text": text, "confidence": turn.confidence}
        )

        # ── INTERCEPT FOR PENDING LATCH-WINDOW CONFIRMATIONS ──
        from backend.tools.policy import PENDING_CONFIRMATIONS

        if turn.session_id in PENDING_CONFIRMATIONS:
            pending = PENDING_CONFIRMATIONS[turn.session_id]
            auth_speaker = pending["speaker_id"]

            # Verify if the speaker says "yes" or "confirm" or "approve"
            is_affirmative = any(
                word in normalized_text for word in ["yes", "confirm", "approve", "go ahead", "do it"]
            )

            if is_affirmative:
                # Resolve generic speaker to the authorized speaker
                speaking_user = turn.speaker_id or "You"
                if speaking_user.lower() in ("you", "unknown", "unregistered", "spk-unknown", ""):
                    speaking_user = auth_speaker

                # Probabilistic voice match: check if speaking cluster matches the initiator
                if speaking_user.lower() == auth_speaker.lower():
                    logger.info(
                        f"[policy] Latch window CONFIRMED! Speaker '{speaking_user}' successfully authorized action '{pending['tool']}'."
                    )
                    pending["confirmed"] = True
                    pending["event"].set()

                    # Prevent forwarding "yes confirm" to LLM to avoid side-effects
                    await session_manager.transition(turn.session_id, SessionState.LISTENING)
                    return
                else:
                    logger.warning(
                        f"[policy] Latch window Impersonation Blocked! Speaker '{turn.speaker_id}' attempted to confirm action '{pending['tool']}', but only '{auth_speaker}' is authorized."
                    )
                    await self.bus.emit_event(
                        "tool_blocked",
                        {
                            "tool": pending["tool"],
                            "speaker": turn.speaker_id,
                            "reason": "impersonation_detected",
                        },
                        turn.session_id,
                    )
                    # Force-close the confirmation as a failure
                    pending["confirmed"] = False
                    pending["event"].set()
                    await session_manager.transition(turn.session_id, SessionState.LISTENING)
                    return

        # Emit transcript to browser immediately
        await self.bus.emit_event(
            "transcript",
            {
                "text": text,
                "speaker": turn.speaker_id or "You",
                "role": turn.role or "user",
                "confidence": round(turn.confidence, 3),
                "timestamp": turn.timestamp,
            },
            turn.session_id,
        )

        # Live customer-sentiment scoring for the Customer Resolution dashboard
        # — only in the customer-care usecase, only for customer (non-PILOT)
        # turns, and fire-and-forget so sentiment inference never delays
        # routing the turn to the Front LLM.
        try:
            state = get_state(turn.session_id)
            if getattr(state, "usecase", "") == "customercare" and (turn.role or "").upper() != "PILOT":
                asyncio.create_task(_score_sentiment(turn.session_id, text, turn.timestamp))
        except Exception as e:
            logger.warning(f"sentiment dispatch skipped: {e}")

        # Forward to front LLM
        await self.bus.transcript_q.put(span)

        # → back to LISTENING (front LLM may override to DELEGATING/SPEAKING)
        await session_manager.transition(turn.session_id, SessionState.LISTENING)

    async def _transcribe_only(self, turn: LabeledTurn, text: str):
        """Meeting-usecase path for a non-wake-word utterance: still show it
        live in the UI and keep it in the session's ring buffer (so an
        eventual "summarize this meeting" request has the full conversation
        to work from) — but deliberately does NOT put it on transcript_q, so
        it never reaches the Front LLM and PILOT never tries to respond to
        two people just talking to each other."""
        from backend.core.session_state import get_state

        get_state(turn.session_id).add_span(
            {"speaker": turn.speaker_id, "role": turn.role, "text": text, "confidence": turn.confidence}
        )
        await self.bus.emit_event(
            "transcript",
            {
                "text": text,
                "speaker": turn.speaker_id or "You",
                "role": turn.role or "user",
                "confidence": round(turn.confidence, 3),
                "timestamp": turn.timestamp,
            },
            turn.session_id,
        )


async def _score_sentiment(session_id: str, text: str, timestamp: float):
    """Analyze one customer turn, store it on the session, and push a
    sentiment_update to the Customer Resolution dashboard."""
    from backend.core.session_state import get_state
    from backend.queues.bus import bus
    from backend.services.sentiment import sentiment_provider
    try:
        result = await sentiment_provider.analyze(text)
        result["timestamp"] = timestamp
        get_state(session_id).add_sentiment(result)
        await bus.emit_event("sentiment_update", result, session_id)
    except Exception as e:
        logger.warning(f"sentiment scoring failed: {e}")
