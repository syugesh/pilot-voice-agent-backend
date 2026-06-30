"""
Audio fanout — broadcasts incoming PCM chunks to all pipeline subscribers.
Reads from raw_audio_q, routes by session_id.
"""
import asyncio
import logging
from queues.bus import bus, RawAudioChunk

logger = logging.getLogger("pilot.fanout")


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
