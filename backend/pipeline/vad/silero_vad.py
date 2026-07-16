"""
Silero VAD — energy gate with hard limits.
MAX_TURN_SECONDS: force-flush if turn exceeds this (prevents 55s mega-turns).
MIN_SPEECH_FRAMES: ignore noise bursts shorter than this.
"""

import logging
import struct
import time

from backend.queues.bus import QueueBus, RawAudioChunk, TurnSegment

logger = logging.getLogger("pilot.silero")

ENERGY_THRESHOLD = 1250  # RMS — Raised to 1250 to filter keyboard clacking, fans, coolers, and ambient background noises
SILENCE_FRAMES_TO_END = 15  # 15 × 30ms = 450ms silence → end turn
MIN_SPEECH_FRAMES = 12  # Raised to 12 frames (360ms) to ensure short transients/clicks/gate-noise are rejected
MAX_TURN_SECONDS = 12.0  # Hard cap — flush after 12s regardless


class SileroVADWorker:
    def __init__(self, bus: QueueBus):
        self.bus = bus
        self._in_speech = False
        self._buffer: list[bytes] = []
        self._speech_start = 0.0
        self._silence_cnt = 0
        self._speech_cnt = 0

    def _rms(self, pcm: bytes) -> float:
        if len(pcm) < 2:
            return 0.0
        n = len(pcm) // 2
        samples = struct.unpack(f"{n}h", pcm[: n * 2])

        # Simple high-pass transient filter to reject sharp single-frame impacts like keyboard clicks
        # We calculate the zero-crossing rate of the audio segment.
        # Voice has lower zero-crossings than sharp high-frequency transient clacks/noise.
        crossings = 0
        for i in range(1, len(samples)):
            if (samples[i] >= 0 and samples[i - 1] < 0) or (samples[i] < 0 and samples[i - 1] >= 0):
                crossings += 1
        zcr = crossings / len(samples)

        # Reject very high zero-crossing rates (which represent friction, keys, and high-frequency noise)
        if zcr > 0.35:
            return 0.0

        return (sum(s * s for s in samples) / n) ** 0.5

    async def _emit(self, session_id: str):
        if self._speech_cnt >= MIN_SPEECH_FRAMES:
            pcm = b"".join(self._buffer)
            seg = TurnSegment(
                pcm=pcm,
                session_id=session_id,
                timestamp=self._speech_start,
                duration_ms=(time.time() - self._speech_start) * 1000,
            )
            await self.bus.turn_q.put(seg)
            logger.debug(f"Turn: {seg.duration_ms:.0f}ms frames={self._speech_cnt}")
        self._in_speech = False
        self._buffer = []
        self._speech_cnt = 0
        self._silence_cnt = 0

    async def run(self):
        logger.info("SileroVAD worker started")
        while True:
            chunk: RawAudioChunk = await self.bus.raw_audio_q.get()
            frame_size = 960  # 30ms at 16kHz × 2 bytes
            for i in range(0, len(chunk.pcm), frame_size):
                frame = chunk.pcm[i : i + frame_size]
                if len(frame) < frame_size:
                    continue
                await self._process(frame, chunk.session_id)

    async def _process(self, frame: bytes, session_id: str):
        is_speech = self._rms(frame) > ENERGY_THRESHOLD

        if is_speech:
            if not self._in_speech:
                self._in_speech = True
                self._speech_start = time.time()
                self._buffer = []
                self._silence_cnt = 0
                self._speech_cnt = 0
            self._buffer.append(frame)
            self._speech_cnt += 1
            self._silence_cnt = 0

            # Hard cap — prevent mega-turns
            if (time.time() - self._speech_start) > MAX_TURN_SECONDS:
                logger.debug("Max turn length reached — force flush")
                await self._emit(session_id)
        else:
            if self._in_speech:
                self._silence_cnt += 1
                self._buffer.append(frame)
                if self._silence_cnt >= SILENCE_FRAMES_TO_END:
                    await self._emit(session_id)
