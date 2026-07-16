"""
Diarization + identity resolution — combined into one step. Separating
"who spoke" (diarization) from "which enrolled account is that" (identity)
across two queue hops added latency and complexity with no benefit, since
identity resolution always immediately follows diarization for every turn
anyway — merged here so a turn goes straight from diar_q to labeled_turn_q.
"""

import asyncio
import logging

from backend.queues.bus import LabeledTurn, QueueBus, TurnSegment

logger = logging.getLogger("pilot.diarizer")


class DiarizerWorker:
    def __init__(self, bus: QueueBus):
        self.bus = bus
        # Per-session memory of which diarized voice cluster (spk-N) was
        # heard FIRST — presumed to be that session's owner (the person
        # actually running PILOT). The biometric matcher in
        # services/enrollment.py is unreliable enough that even a
        # genuinely-enrolled second speaker often fails to cross
        # COSINE_THRESHOLD, and without this, EVERY unmatched voice used to
        # collapse to the same "You" fallback — silently mislabeling a real
        # second person as the session owner instead of as unidentified.
        self._session_primary_label: dict[str, str] = {}

    def _load(self):
        from backend.services.diarizer import pyannote_provider

        pyannote_provider.load()

    async def run(self):
        # Loads the real speaker-embedding model (see services/diarizer.py's
        # SpeakerEmbedder) once at worker startup — same pattern as
        # ASRWorker._load() for the Whisper model. asyncio.to_thread keeps
        # the (possibly slow, first-run-downloads-a-checkpoint) load off the
        # event loop.
        await asyncio.to_thread(self._load)
        logger.info("Diarizer worker started")
        while True:
            seg: TurnSegment = await self.bus.diar_q.get()  # ← reads diar_q
            try:
                from backend.services.diarizer import pyannote_provider

                # Pyannote.audio-shaped unsupervised speaker segmentation/
                # clustering: groups the audio into speaker profiles and
                # extracts a temporary label (e.g. spk-0 for Speaker 1).
                segments = await pyannote_provider.segment(seg.pcm, session_id=seg.session_id)
                label = segments[0].speaker_label if segments else "spk-0"
            except Exception as e:
                logger.error(f"Diarizer error: {e}")
                label = "spk-0"

            # Identity resolution — match this segment's voice against
            # enrolled profiles (see services/enrollment.py) to attach a
            # real speaker_id/role right here, rather than a separate
            # pipeline stage.
            speaker_id: str | None = None
            role: str | None = None
            confidence = 0.0
            try:
                from backend.services.enrollment import identify_speaker

                speaker_id, role, confidence = await identify_speaker(seg.pcm)
            except Exception as e:
                logger.error(f"Identity error: {e}")
                confidence = 0.8

            if speaker_id:
                role = role or "user"
            else:
                primary_label = self._session_primary_label.setdefault(seg.session_id, label)
                speaker_id = "You" if label == primary_label else f"Unknown speaker ({label})"
                role = role or "user"

            labeled = LabeledTurn(
                pcm=seg.pcm,
                session_id=seg.session_id,
                timestamp=seg.timestamp,
                speaker_label=label,
                speaker_id=speaker_id,
                role=role,
                confidence=confidence,
                cached_text=seg.cached_text,
            )
            await self.bus.labeled_turn_q.put(labeled)  # ← writes labeled_turn_q directly
