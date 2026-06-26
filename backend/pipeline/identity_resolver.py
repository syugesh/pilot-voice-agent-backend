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
            try:
                from services.enrollment import identify_speaker
                from core.session_state import get_state
                speaker_id, role, confidence, msg = await identify_speaker(turn.pcm)
                state = get_state(turn.session_id)
                if speaker_id and role:
                    turn.speaker_id = speaker_id
                    turn.role = role
                else:
                    # Voice ID failed — fall back to the JWT-authenticated user's role
                    fallback = state.fallback_role or "user"
                    turn.speaker_id = "You"
                    turn.role = fallback
                    logger.debug(f"Voice ID failed ({msg}), using fallback role={fallback!r}")
                turn.confidence = confidence
            except Exception as e:
                logger.error(f"Identity error: {e}")
                turn.speaker_id = "You"
                turn.role = "user"
                turn.confidence = 0.8

            await self.bus.labeled_turn_q.put(turn)   # ← writes labeled_turn_q
