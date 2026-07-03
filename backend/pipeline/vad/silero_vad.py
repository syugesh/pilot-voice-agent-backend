"""
Silero VAD — neural speech-probability gate replacing energy threshold.
Frame size: 512 samples @ 16kHz = 32ms (Silero requirement).
Falls back to energy threshold if the model fails to load.

Streaming diarization:
  Every EMBED_WINDOW_FRAMES speech frames (512ms) an embedding is extracted in a
  background thread and stored as an asyncio.Task on the _VADState.  When a turn
  ends those tasks travel with the TurnSegment so the DiarizerWorker can await
  them instead of running a single batch extraction after the fact.
"""
import asyncio, time, logging, struct
from queues.bus import QueueBus, RawAudioChunk, TurnSegment

logger = logging.getLogger("pilot.silero")

# ── Tuning knobs ──────────────────────────────────────────────────────────────
SPEECH_PROB_THRESHOLD = 0.5    # Silero probability to count a frame as speech
ENERGY_FALLBACK_RMS   = 500    # RMS used if neural model didn't load
SILENCE_FRAMES_TO_END = 9      # 9 × 32ms = 288ms silence → end turn
                                # (was 480ms — too long a gap let back-to-back
                                # different speakers get merged into one turn,
                                # so diarization/identification only ever saw
                                # one blended speaker label for both people)
MIN_SPEECH_FRAMES     = 10     # 10 × 32ms = 320ms minimum speech to emit
MAX_TURN_SECONDS      = 12.0   # hard cap
MIN_AVG_PROB          = 0.45   # whole-turn average speech-prob gate (neural mode)
MIN_AVG_RMS           = 600    # whole-turn average RMS gate (fallback mode)

# Silero requires exactly 512 samples per chunk at 16 kHz
FRAME_SAMPLES = 512
FRAME_BYTES   = FRAME_SAMPLES * 2   # int16 → 2 bytes each = 1024 bytes

# Streaming diarization: extract one embedding per this many speech frames.
# 16 frames × 32ms = 512ms per window — enough for WeSpeaker ECAPA-TDNN.
EMBED_WINDOW_FRAMES = 16

# ── Model loading (lazy, once) ────────────────────────────────────────────────
_model        = None
_use_neural   = False

def _load():
    global _model, _use_neural
    try:
        from silero_vad import load_silero_vad
        _model      = load_silero_vad()
        _use_neural = True
        logger.info("Silero VAD: neural model loaded (speech-probability mode)")
    except Exception as e:
        logger.warning(f"Silero VAD neural model unavailable ({e}) — energy-threshold fallback")
        _use_neural = False

# ── Per-session VAD state ─────────────────────────────────────────────────────
class _VADState:
    __slots__ = (
        "in_speech", "buffer", "speech_start", "silence_cnt", "speech_cnt",
        "prob_sum", "rms_sum", "rms_n", "rms_min", "rms_max",
        # Streaming diarization fields
        "embed_win_buf",    # frames accumulating for the next embedding window
        "embed_win_cnt",    # speech-frame count in current window
        "embed_futures",    # list of (start_sec_into_turn, asyncio.Task)
    )
    def __init__(self):
        self.in_speech    = False
        self.buffer:      list[bytes] = []
        self.speech_start = 0.0
        self.silence_cnt  = 0
        self.speech_cnt   = 0
        self.prob_sum     = 0.0   # accumulated speech probability (neural)
        self.rms_sum      = 0.0   # accumulated RMS (fallback)
        self.rms_n        = 0
        self.rms_min      = 1e9
        self.rms_max      = 0.0
        self.embed_win_buf:   list[bytes] = []
        self.embed_win_cnt:   int         = 0
        self.embed_futures:   list        = []  # (start_sec, asyncio.Task)


# ── Main worker ───────────────────────────────────────────────────────────────
class SileroVADWorker:
    def __init__(self, bus: QueueBus):
        self.bus = bus
        self._states: dict[str, _VADState] = {}
        _load()

    def _state(self, sid: str) -> _VADState:
        if sid not in self._states:
            self._states[sid] = _VADState()
        return self._states[sid]

    # ── Speech probability (neural) ───────────────────────────────────────────
    def _speech_prob(self, frame: bytes) -> float:
        import torch
        samples = struct.unpack(f"{FRAME_SAMPLES}h", frame[:FRAME_BYTES])
        tensor  = torch.FloatTensor(samples) / 32768.0
        return float(_model(tensor, 16000).item())

    # ── Energy RMS fallback ───────────────────────────────────────────────────
    @staticmethod
    def _rms(frame: bytes) -> float:
        n = len(frame) // 2
        samples = struct.unpack(f"{n}h", frame[:n * 2])
        return (sum(s * s for s in samples) / n) ** 0.5

    # ── Streaming embedding extraction ───────────────────────────────────────
    def _schedule_embed(self, st: _VADState, start_sec: float):
        """Launch background embedding extraction for the current window buffer."""
        chunk = b"".join(st.embed_win_buf)
        st.embed_win_buf = []
        st.embed_win_cnt = 0
        if not chunk:
            return
        from services.enrollment import embed_provider
        task = asyncio.create_task(
            asyncio.to_thread(embed_provider.extract, chunk)
        )
        st.embed_futures.append((start_sec, task))

    # ── Turn emit ─────────────────────────────────────────────────────────────
    async def _emit(self, session_id: str, st: _VADState):
        n = st.rms_n or 1
        avg_prob = st.prob_sum / n
        avg_rms  = st.rms_sum / n

        # Gate: require sustained speech (neural) or high energy (fallback)
        quality_ok = (avg_prob >= MIN_AVG_PROB) if _use_neural else (avg_rms >= MIN_AVG_RMS)

        if st.speech_cnt >= MIN_SPEECH_FRAMES and quality_ok:
            # Flush any partial embedding window that hasn't hit EMBED_WINDOW_FRAMES yet
            if st.embed_win_buf:
                self._schedule_embed(st, time.time() - st.speech_start)

            pcm = b"".join(st.buffer)
            seg = TurnSegment(
                pcm=pcm,
                session_id=session_id,
                timestamp=st.speech_start,
                duration_ms=(time.time() - st.speech_start) * 1000,
                embed_futures=st.embed_futures.copy(),   # streaming diarization payload
            )
            await self.bus.turn_q.put(seg)
            mode = "neural" if _use_neural else "energy-fallback"
            n_wins = len(st.embed_futures)
            logger.info(
                f"[{session_id[:8]}] turn {seg.duration_ms:.0f}ms "
                f"frames={st.speech_cnt} avg_prob={avg_prob:.2f} "
                f"embed_windows={n_wins} ({mode})"
            )
        elif st.speech_cnt > 0:
            # Cancel any pending embed tasks — turn was discarded
            for _, task in st.embed_futures:
                task.cancel()
            logger.debug(
                f"[{session_id[:8]}] turn dropped — frames={st.speech_cnt} "
                f"avg_prob={avg_prob:.2f} avg_rms={avg_rms:.0f}"
            )

        # Reset turn state
        st.in_speech      = False
        st.buffer         = []
        st.speech_cnt     = 0
        st.silence_cnt    = 0
        st.prob_sum       = 0.0
        st.rms_sum        = 0.0
        st.rms_n          = 0
        st.rms_min        = 1e9
        st.rms_max        = 0.0
        st.embed_win_buf  = []
        st.embed_win_cnt  = 0
        st.embed_futures  = []

    # ── Main loop ─────────────────────────────────────────────────────────────
    async def run(self):
        logger.info(f"SileroVAD worker started — mode: {'neural' if _use_neural else 'energy-fallback'}")
        while True:
            chunk: RawAudioChunk = await self.bus.raw_audio_q.get()
            for i in range(0, len(chunk.pcm), FRAME_BYTES):
                frame = chunk.pcm[i: i + FRAME_BYTES]
                if len(frame) < FRAME_BYTES:
                    continue
                await self._process(frame, chunk.session_id)

    async def _process(self, frame: bytes, session_id: str):
        st = self._state(session_id)

        # Speech detection: neural prob or energy RMS
        if _use_neural:
            prob      = self._speech_prob(frame)
            is_speech = prob > SPEECH_PROB_THRESHOLD
        else:
            rms       = self._rms(frame)
            prob      = min(rms / 1000.0, 1.0)   # normalise RMS to 0-1 for logging
            is_speech = rms > ENERGY_FALLBACK_RMS

        if is_speech:
            if not st.in_speech:
                # Reset Silero LSTM state at the start of each new turn so the model
                # begins fresh — avoids stale context from previous sessions/turns
                if _use_neural:
                    _model.reset_states()
                st.in_speech      = True
                st.speech_start   = time.time()
                st.buffer         = []
                st.silence_cnt    = 0
                st.speech_cnt     = 0
                st.prob_sum       = 0.0
                st.rms_sum        = 0.0
                st.rms_n          = 0
                st.embed_win_buf  = []
                st.embed_win_cnt  = 0
                st.embed_futures  = []

            st.buffer.append(frame)
            st.speech_cnt += 1
            st.silence_cnt = 0
            st.prob_sum   += prob
            rms = self._rms(frame)
            st.rms_sum    += rms
            st.rms_n      += 1
            st.rms_min     = min(st.rms_min, rms)
            st.rms_max     = max(st.rms_max, rms)

            # Streaming diarization: accumulate frames for the embedding window
            st.embed_win_buf.append(frame)
            st.embed_win_cnt += 1
            if st.embed_win_cnt >= EMBED_WINDOW_FRAMES:
                # 16 × 32ms = 512ms of speech — enough for WeSpeaker ECAPA-TDNN
                elapsed = (st.speech_cnt - st.embed_win_cnt) * 0.032  # start of this window
                self._schedule_embed(st, elapsed)

            if (time.time() - st.speech_start) > MAX_TURN_SECONDS:
                logger.info(f"[{session_id[:8]}] force-flush after {MAX_TURN_SECONDS}s")
                await self._emit(session_id, st)
        else:
            if st.in_speech:
                st.silence_cnt += 1
                st.buffer.append(frame)
                if st.silence_cnt >= SILENCE_FRAMES_TO_END:
                    await self._emit(session_id, st)
