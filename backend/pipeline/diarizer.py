"""
who spoke when or separating different voices in the audio stream).

"""

import asyncio
import logging

from backend.queues.bus import LabeledTurn, QueueBus, TurnSegment

logger = logging.getLogger("pilot.diarizer")


class DiarizerWorker:
    def __init__(self, bus: QueueBus):
        self.bus = bus

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

                # This service uses Pyannote.audio (an unsupervised speaker segmentation and clustering model) to analyze the acoustic features of the audio.
                segments = await pyannote_provider.segment(seg.pcm, session_id=seg.session_id)
                # It groups the audio into speaker profiles and extracts a temporary label (e.g., spk-0 for Speaker 1, spk-1 for Speaker 2).
                label = segments[0].speaker_label if segments else "spk-0"

            except Exception as e:
                logger.error(f"Diarizer error: {e}")
                label = "spk-0"
                # Creates a LabeledTurn object containing the original audio, the determined speaker label, and placeholder values for speaker_id and role (which will be filled in by the subsequent identity pipeline stage).

            # It attaches the temporary speaker label (e.g., speaker_label="spk-0"), while leaving speaker_id (the actual user name/email) and role empty (None), as this will be resolved by the next worker in the pipeline.
            labeled = LabeledTurn(
                pcm=seg.pcm,
                session_id=seg.session_id,
                timestamp=seg.timestamp,
                speaker_label=label,
                speaker_id=None,
                role=None,
                confidence=0.0,
                cached_text=seg.cached_text,
            )
            await self.bus.identity_q.put(labeled)  # ← writes identity_q


# now it will go to asr for identity resolver.
