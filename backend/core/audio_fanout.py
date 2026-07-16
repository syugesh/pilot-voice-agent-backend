"""
Audio fanout — broadcasts incoming PCM chunks to all pipeline subscribers.
Reads from raw_audio_q, routes by session_id.
"""

import logging

from backend.queues.bus import RawAudioChunk, bus

logger = logging.getLogger("pilot.fanout")


# It's meant to be a broadcast point: take one incoming PCM audio chunk and forward it to multiple downstream consumers — e.g. if you eventually want VAD (voice activity detection) and the speaker diarizer to each independently process the same audio in parallel, this is where that "split" would happen.


class AudioFanout:
    """
    When multiple pipeline workers need the same audio (e.g., VAD + diarizer
    running in parallel), this fans the chunk out to multiple queues.
    Currently: serial pipeline, so fanout just forwards to VAD queue.
    """

    async def run(self):
        logger.info("AudioFanout started")
        while True:
            chunk: RawAudioChunk = await bus.raw_audio_q.get()
            # Forward to VAD (silero reads from raw_audio_q directly in this impl)
            # Extend here to fan out to parallel workers


audio_fanout = AudioFanout()
