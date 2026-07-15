"""
asynchronous pipeline worker responsible for identifying who is speaking using voice biometrics. It sits in the middle of PILOT's real-time audio pipeline.
"""

import logging

from backend.queues.bus import LabeledTurn, QueueBus

logger = logging.getLogger("pilot.identity")


class IdentityResolverWorker:
    def __init__(self, bus: QueueBus):
        self.bus = bus
        # Per-session memory of which diarized voice cluster (spk-N, from
        # DiarizerWorker) was heard FIRST — presumed to be that session's
        # owner (the person actually running PILOT). The biometric matcher
        # in services/enrollment.py is unreliable enough that even a
        # genuinely-enrolled second speaker often fails to cross
        # COSINE_THRESHOLD, and without this, EVERY unmatched voice used to
        # collapse to the same "You" fallback — silently mislabeling a real
        # second person as the session owner instead of as unidentified.
        self._session_primary_label: dict[str, str] = {}

    async def run(self):
        logger.info("IdentityResolver worker started")
        # The previous worker in the pipeline (the Diarizer) places active audio segments into the identity_q queue.
        while True:
            turn: LabeledTurn = await self.bus.identity_q.get()  # ← reads identity_q
            # The resolver reads these segments as LabeledTurn objects containing raw PCM audio.
            try:
                from backend.services.enrollment import identify_speaker

                speaker_id, role, confidence = await identify_speaker(turn.pcm)
                # It calls the identify_speaker service with the audio.
                if speaker_id:
                    turn.speaker_id = speaker_id
                else:
                    primary_label = self._session_primary_label.setdefault(
                        turn.session_id, turn.speaker_label
                    )
                    turn.speaker_id = (
                        "You" if turn.speaker_label == primary_label
                        else f"Unknown speaker ({turn.speaker_label})"
                    )
                turn.role = role or "user"
                turn.confidence = confidence

            # if the biometric match fails or throwns it uses safe defaults such that it can recognize an unknown person also.
            except Exception as e:
                logger.error(f"Identity error: {e}")
                primary_label = self._session_primary_label.setdefault(
                    turn.session_id, turn.speaker_label
                )
                turn.speaker_id = "You" if turn.speaker_label == primary_label else f"Unknown speaker ({turn.speaker_label})"
                turn.role = "user"
                turn.confidence = 0.8

            await self.bus.labeled_turn_q.put(turn)  # ← writes labeled_turn_q
