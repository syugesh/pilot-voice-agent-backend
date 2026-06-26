import asyncio, logging
from queues.bus import QueueBus, TurnSegment, LabeledTurn

logger = logging.getLogger("pilot.diarizer")


def _dominant_speaker(segments) -> str:
    """Return the speaker label that has the most total duration."""
    if not segments:
        return "spk-0"
    durations: dict[str, float] = {}
    for s in segments:
        durations[s.speaker_label] = durations.get(s.speaker_label, 0) + (s.end - s.start)
    return max(durations, key=durations.get)


class DiarizerWorker:
    def __init__(self, bus: QueueBus):
        self.bus = bus
        self._prev_label: dict[str, str] = {}  # session_id → last speaker label

    async def run(self):
        logger.info("Diarizer worker started")
        while True:
            seg: TurnSegment = await self.bus.diar_q.get()
            try:
                from services.diarizer import pyannote_provider, _energy_diarize
                prev = self._prev_label.get(seg.session_id, "spk-0")
                if pyannote_provider._pipeline is not None:
                    segments = await pyannote_provider.segment(seg.pcm)
                    label = _dominant_speaker(segments)
                else:
                    segs = _energy_diarize(seg.pcm, prev_speaker=prev)
                    label = _dominant_speaker(segs)
                self._prev_label[seg.session_id] = label
            except Exception as e:
                logger.error(f"Diarizer error: {e}")
                label = self._prev_label.get(seg.session_id, "spk-0")

            labeled = LabeledTurn(
                pcm=seg.pcm, session_id=seg.session_id, timestamp=seg.timestamp,
                speaker_label=label, speaker_id=None, role=None, confidence=0.0
            )
            await self.bus.identity_q.put(labeled)
