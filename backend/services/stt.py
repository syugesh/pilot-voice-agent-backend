"""
STT — auto-selects backend at load time:
  • Apple Silicon (arm64 Darwin)  → mlx-whisper  (Neural Engine / GPU, unified memory)
  • Everything else               → faster-whisper (CPU int8)
Both use distil-large-v3 for quality parity.
"""
import asyncio, logging, platform, re
logger = logging.getLogger("pilot.stt")

# ── Hardware probe ────────────────────────────────────────────────────────────

def _is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"

# ── Hallucination filter (shared) ─────────────────────────────────────────────

_HALLUCINATIONS = {
    "thank you", "thanks for watching", "thanks for listening",
    "bye", "goodbye", "see you", "you", ".", "..", "...",
    "thanks", "thank you.", "thank you for watching.",
    "thanks for watching.", "please subscribe",
    "subtitles by", "transcribed by", "[music]", "[applause]",
    "[blank_audio]", "[ silence ]", "silence",
    # Common Whisper silence/noise hallucinations
    "i don't know", "i don't know.", "hmm", "hm", "um", "uh",
    "okay", "ok", "alright", "right", "sure", "yes", "no",
    "ah", "oh", "uh huh", "mm", "mm-hmm",
}

# CJK unicode ranges — Whisper hallucinates these on silence/noise
_CJK_RE = re.compile(r'[一-鿿぀-ヿ가-힯]')

def _has_word_loop(text: str, min_repeats: int = 3) -> bool:
    """Detect 'topics topics topics...' style repetition loops."""
    words = text.lower().split()
    if len(words) < min_repeats:
        return False
    # Check if any single word repeats consecutively min_repeats+ times
    for i in range(len(words) - min_repeats + 1):
        if len(set(words[i:i + min_repeats])) == 1:
            return True
    # Check if a short phrase (2-3 words) repeats
    for phrase_len in (2, 3):
        if len(words) >= phrase_len * 2:
            phrase = " ".join(words[:phrase_len])
            full   = " ".join(words)
            if full.count(phrase) >= 3:
                return True
    return False

def _clean(text: str) -> str:
    """Return cleaned text or '' if it should be discarded."""
    t = text.strip()
    if not t or len(t) < 3:
        return ""
    tl = t.lower().strip(" .")
    if tl in _HALLUCINATIONS:
        logger.debug(f"Hallucination filtered: {t!r}")
        return ""
    # Drop anything with CJK characters (hallucinated on silence)
    if _CJK_RE.search(t):
        logger.debug(f"CJK hallucination filtered: {t!r}")
        return ""
    # Drop word repetition loops
    if _has_word_loop(t):
        logger.debug(f"Word-loop hallucination filtered: {t!r}")
        return ""
    # Drop if >60% of the text is a single repeated word
    words = tl.split()
    if len(words) >= 4:
        most_common_count = max(words.count(w) for w in set(words))
        if most_common_count / len(words) > 0.6:
            logger.debug(f"High-repetition hallucination filtered: {t!r}")
            return ""
    return t


# ── Provider ──────────────────────────────────────────────────────────────────

class WhisperSTTProvider:
    def __init__(self):
        self._backend: str | None = None   # "mlx" | "faster_whisper"
        self._fw_model = None              # faster_whisper.WhisperModel instance
        self._mlx_repo: str | None = None  # HF repo string for mlx-whisper

    # ── Load ──────────────────────────────────────────────────────────────────

    def load(self):
        if _is_apple_silicon():
            if self._try_mlx():
                return
            logger.warning("Apple Silicon detected but mlx-whisper unavailable — falling back to faster-whisper")
        self._load_faster_whisper()

    def _try_mlx(self) -> bool:
        import importlib.util
        if importlib.util.find_spec("mlx_whisper") is None:
            logger.warning("mlx-whisper not installed — run: pip install mlx-whisper")
            return False
        try:
            # Warm up: tiny transcribe to confirm the model downloads / loads OK
            import importlib
            mlx = importlib.import_module("mlx_whisper")
            import numpy as np
            mlx.transcribe(np.zeros(3200, dtype=np.float32),
                           path_or_hf_repo="mlx-community/distil-whisper-large-v3",
                           verbose=False)
            self._backend  = "mlx"
            self._mlx_repo = "mlx-community/distil-whisper-large-v3"
            logger.info("STT backend: mlx-whisper distil-large-v3 (Apple Silicon Neural Engine)")
            return True
        except Exception as e:
            logger.warning(f"mlx-whisper load/warmup error: {e}")
            return False

    def _load_faster_whisper(self):
        from core.config import settings
        preferred = getattr(settings, "WHISPER_MODEL", "distil-large-v3")
        for name in [preferred, "small", "base"]:
            try:
                from faster_whisper import WhisperModel
                self._fw_model = WhisperModel(name, device="cpu", compute_type="int8")
                self._backend  = "faster_whisper"
                logger.info(f"STT backend: faster-whisper {name} (CPU int8)")
                return
            except Exception as e:
                logger.warning(f"faster-whisper {name!r} failed: {e}")
        logger.error("STT: all Whisper models failed to load")

    # ── Transcribe ────────────────────────────────────────────────────────────

    def _do_transcribe(self, pcm: bytes) -> str:
        import numpy as np
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

        if len(audio) < 4800:          # < 300 ms
            return ""
        rms = float((audio ** 2).mean() ** 0.5)
        if rms < 0.015:                # silence / background noise (0.015 ≈ 490 int16 RMS)
            return ""

        if self._backend == "mlx":
            return self._transcribe_mlx(audio)
        if self._backend == "faster_whisper":
            return self._transcribe_fw(audio)
        return ""

    def _transcribe_mlx(self, audio) -> str:
        try:
            import importlib
            mlx_whisper = importlib.import_module("mlx_whisper")
            result = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=self._mlx_repo,
                language="en",
                verbose=False,
                condition_on_previous_text=False,  # prevents hallucination cascades
                no_speech_threshold=0.6,            # stricter: discard low-confidence segments
                compression_ratio_threshold=2.4,    # drop repetition-loop outputs
            )
            text = result.get("text", "").strip()
            logger.debug(f"MLX transcribed: {text!r}")
            return _clean(text)
        except Exception as e:
            logger.error(f"MLX transcribe error: {e}")
            return ""

    def _transcribe_fw(self, audio) -> str:
        try:
            segments, _ = self._fw_model.transcribe(
                audio,
                beam_size=1,
                language="en",
                vad_filter=False,
                no_speech_threshold=0.45,
                compression_ratio_threshold=2.4,
                condition_on_previous_text=False,
            )
            text = " ".join(s.text for s in segments).strip()
            logger.debug(f"FW transcribed: {text!r}")
            return _clean(text)
        except Exception as e:
            logger.error(f"faster-whisper transcribe error: {e}")
            return ""

    async def transcribe(self, pcm: bytes) -> str:
        return await asyncio.to_thread(self._do_transcribe, pcm)

    @property
    def backend(self) -> str:
        return self._backend or "not_loaded"


whisper_provider = WhisperSTTProvider()
