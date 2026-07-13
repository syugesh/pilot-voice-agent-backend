import asyncio, logging
from queues.bus import QueueBus, LabeledTurn

logger = logging.getLogger("pilot.identity")

# Below this cosine score, a failed match isn't "probably the session owner,
# just noisy audio" — it's a genuinely different voice. The JWT fallback
# (session owner's real name/role) must only apply in the former case; a
# distinct unenrolled speaker (e.g. a customer on the same call) must never
# be labeled with the session owner's identity just because they're the one
# who happened to be logged in when the session started.
_OWN_VOICE_PLAUSIBLE_SCORE = 0.45


def _unknown_label(usecase: str | None) -> str:
    # Customer-care sessions know their second-voice role by convention —
    # anyone unenrolled on that call is the customer, not a generic unknown.
    if (usecase or "").lower() == "customercare":
        return "Customer"
    return "Unknown Speaker"


class IdentityResolverWorker:
    def __init__(self, bus: QueueBus):
        self.bus = bus

    async def run(self):
        logger.info("IdentityResolver worker started")
        while True:
            turn: LabeledTurn = await self.bus.identity_q.get()   # ← reads identity_q
            from core.session_state import get_state
            state = get_state(turn.session_id)
            try:
                # DiarizerWorker already identified this turn — per-window embeddings
                # averaged via majority vote, which is more robust than a single
                # full-turn embedding. Trust that result; only re-run identification
                # here as a last resort if the diarizer didn't set anything at all
                # (e.g. exception before it could run). Re-running identify_speaker()
                # unconditionally here used to silently discard the diarizer's result
                # every single time, which is what caused speakers to be misidentified
                # even when the streaming path got it right.
                if turn.speaker_id and turn.role:
                    logger.debug(f"[{turn.session_id[:8]}] using diarizer result: "
                                 f"{turn.speaker_id!r} conf={turn.confidence:.2f}")
                else:
                    from services.enrollment import identify_speaker
                    speaker_id, role, confidence, msg = await identify_speaker(turn.pcm)
                    if speaker_id and role:
                        turn.speaker_id = speaker_id
                        turn.role = role
                        turn.confidence = confidence
                    elif confidence >= _OWN_VOICE_PLAUSIBLE_SCORE and state.fallback_name:
                        # Close-but-below-threshold match, and this session has a
                        # known logged-in owner — most likely the owner's own
                        # voice on a quiet/noisy turn, not a second person.
                        fallback = state.fallback_role or "user"
                        turn.speaker_id = state.fallback_name or "You"
                        turn.role = fallback
                        turn.confidence = confidence
                        logger.info(f"[{turn.session_id[:8]}] Voice ID inconclusive ({msg}, "
                                    f"score={confidence:.2f}) — plausibly the logged-in owner, "
                                    f"using {turn.speaker_id!r} role={fallback!r}")
                    else:
                        # Score too low to plausibly be the session owner (or no
                        # owner identity known at all) — this is a different,
                        # unenrolled physical speaker. Label them as such rather
                        # than guessing an identity that isn't theirs.
                        turn.speaker_id = _unknown_label(state.usecase)
                        turn.role = "customer" if turn.speaker_id == "Customer" else "guest"
                        turn.confidence = confidence
                        logger.info(f"[{turn.session_id[:8]}] Voice ID failed ({msg}, "
                                    f"score={confidence:.2f}) — labeling as {turn.speaker_id!r}, "
                                    f"NOT the logged-in owner")
            except Exception as e:
                logger.error(f"Identity error: {e}")
                turn.speaker_id = _unknown_label(state.usecase)
                turn.role = "customer" if turn.speaker_id == "Customer" else "guest"
                turn.confidence = 0.0

            await self.bus.labeled_turn_q.put(turn)   # ← writes labeled_turn_q
