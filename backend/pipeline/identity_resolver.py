import asyncio, logging
from queues.bus import QueueBus, LabeledTurn

logger = logging.getLogger("pilot.identity")

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
                    else:
                        # Voice ID failed — fall back to the JWT-authenticated user's identity
                        fallback = state.fallback_role or "user"
                        turn.speaker_id = state.fallback_name or "You"
                        turn.role = fallback
                        turn.confidence = confidence
                        logger.info(f"[{turn.session_id[:8]}] Voice ID failed ({msg}) — "
                                    f"falling back to logged-in identity {turn.speaker_id!r} role={fallback!r}")
            except Exception as e:
                logger.error(f"Identity error: {e}")
                turn.speaker_id = state.fallback_name or "You"
                turn.role = "user"
                turn.confidence = 0.8

            await self.bus.labeled_turn_q.put(turn)   # ← writes labeled_turn_q
