"""
Silero VAD — energy gate with hard limits.
MAX_TURN_SECONDS: force-flush if turn exceeds this (prevents 55s mega-turns).
MIN_SPEECH_FRAMES: ignore noise bursts shorter than this.
"""
import asyncio, time, logging, struct
from queues.bus import QueueBus, RawAudioChunk, TurnSegment

logger = logging.getLogger("pilot.silero")

ENERGY_THRESHOLD      = 300    # RMS — raise if too sensitive to background noise
SILENCE_FRAMES_TO_END = 15     # 15 × 30ms = 450ms silence → end turn
MIN_SPEECH_FRAMES     = 8      # 8 × 30ms = 240ms minimum — ignore shorter noise
MAX_TURN_SECONDS      = 12.0   # Hard cap — flush after 12s regardless


class _VADState:
    __slots__ = ("in_speech", "buffer", "speech_start", "silence_cnt", "speech_cnt",
                 "rms_min", "rms_max", "rms_sum", "rms_n")
    def __init__(self):
        self.in_speech:   bool       = False
        self.buffer:      list[bytes] = []
        self.speech_start: float     = 0.0
        self.silence_cnt:  int       = 0
        self.speech_cnt:   int       = 0
        self.rms_min: float = 1e9
        self.rms_max: float = 0.0
        self.rms_sum: float = 0.0
        self.rms_n:   int   = 0


class SileroVADWorker:
    def __init__(self, bus: QueueBus):
        self.bus = bus
        self._states: dict[str, _VADState] = {}

    def _state(self, session_id: str) -> _VADState:
        if session_id not in self._states:
            self._states[session_id] = _VADState()
        return self._states[session_id]

    def _rms(self, pcm: bytes) -> float:
        if len(pcm) < 2:
            return 0.0
        n = len(pcm) // 2
        samples = struct.unpack(f"{n}h", pcm[:n*2])
        return (sum(s*s for s in samples) / n) ** 0.5

    async def _emit(self, session_id: str, st: _VADState):
        if st.speech_cnt >= MIN_SPEECH_FRAMES:
            pcm = b"".join(st.buffer)
            seg = TurnSegment(
                pcm=pcm,
                session_id=session_id,
                timestamp=st.speech_start,
                duration_ms=(time.time() - st.speech_start) * 1000,
            )
            await self.bus.turn_q.put(seg)
            avg_rms = (st.rms_sum / st.rms_n) if st.rms_n else 0
            logger.info(
                f"[{session_id[:8]}] turn {seg.duration_ms:.0f}ms frames={st.speech_cnt} "
                f"RMS min={st.rms_min:.0f} avg={avg_rms:.0f} max={st.rms_max:.0f}"
            )
        st.in_speech   = False
        st.buffer      = []
        st.speech_cnt  = 0
        st.silence_cnt = 0
        st.rms_min     = 1e9
        st.rms_max     = 0.0
        st.rms_sum     = 0.0
        st.rms_n       = 0

    async def run(self):
        logger.info("SileroVAD worker started")
        while True:
            chunk: RawAudioChunk = await self.bus.raw_audio_q.get()
            frame_size = 960  # 30ms at 16kHz × 2 bytes
            for i in range(0, len(chunk.pcm), frame_size):
                frame = chunk.pcm[i:i+frame_size]
                if len(frame) < frame_size:
                    continue
                await self._process(frame, chunk.session_id)

    async def _process(self, frame: bytes, session_id: str):
        st = self._state(session_id)
        rms = self._rms(frame)
        is_speech = rms > ENERGY_THRESHOLD

        if is_speech:
            if not st.in_speech:
                st.in_speech    = True
                st.speech_start = time.time()
                st.buffer       = []
                st.silence_cnt  = 0
                st.speech_cnt   = 0
            st.buffer.append(frame)
            st.speech_cnt  += 1
            st.silence_cnt  = 0
            # Track RMS distribution across speech frames
            st.rms_min  = min(st.rms_min, rms)
            st.rms_max  = max(st.rms_max, rms)
            st.rms_sum += rms
            st.rms_n   += 1

            # Hard cap — prevent mega-turns
            if (time.time() - st.speech_start) > MAX_TURN_SECONDS:
                avg_rms = (st.rms_sum / st.rms_n) if st.rms_n else 0
                logger.info(
                    f"[{session_id[:8]}] force-flush after {MAX_TURN_SECONDS}s — "
                    f"RMS min={st.rms_min:.0f} avg={avg_rms:.0f} max={st.rms_max:.0f}"
                )
                await self._emit(session_id, st)
        else:
            if st.in_speech:
                st.silence_cnt += 1
                st.buffer.append(frame)
                if st.silence_cnt >= SILENCE_FRAMES_TO_END:
                    await self._emit(session_id, st)
