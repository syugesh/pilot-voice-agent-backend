"""
STT — faster-whisper with hallucination filtering.
Filters out common Whisper hallucinations on silence/noise.
"""
import asyncio, logging
logger = logging.getLogger("pilot.stt")

# Whisper hallucinates these on silence — filter them out
_HALLUCINATIONS = {
    "thank you", "thanks for watching", "thanks for listening",
    "bye", "goodbye", "see you", "you", ".", "..", "...",
    "thanks", "thank you.", "thank you for watching.",
    "thanks for watching.", "please subscribe",
    "subtitles by", "transcribed by", "[music]", "[applause]",
    "[blank_audio]", "[ Silence ]", "silence",
}

_FALLBACKS = ["distil-large-v3", "small", "base"]


class WhisperSTTProvider:
    def __init__(self):
        self._model = None

    def load(self):
        from core.config import settings
        preferred = getattr(settings, "WHISPER_MODEL", "distil-large-v3")
        order = [preferred] + [m for m in _FALLBACKS if m != preferred]
        for name in order:
            try:
                from faster_whisper import WhisperModel
                self._model = WhisperModel(name, device="cpu", compute_type="int8")
                logger.info(f"Whisper loaded: {name}")
                return
            except Exception as e:
                logger.warning(f"Whisper {name} failed: {e} — trying next")
        logger.error("All Whisper models failed")

    def _do_transcribe(self, pcm: bytes) -> str:
        if self._model is None:
            return ""
        import numpy as np
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if len(audio) < 3200:   # < 200ms — skip
            return ""

        # Check audio energy — reject silent segments
        rms = float((audio ** 2).mean() ** 0.5)
        if rms < 0.005:         # essentially silence
            return ""

        segments, info = self._model.transcribe(
            audio, beam_size=1, language="en",
            vad_filter=False,
            no_speech_threshold=0.45,  # slightly more permissive than default 0.6
            compression_ratio_threshold=2.4,
            condition_on_previous_text=False,  # prevent context contamination
        )
        text = " ".join(s.text for s in segments).strip()

        # Filter hallucinations
        if text.lower().strip(" .") in _HALLUCINATIONS:
            logger.debug(f"Hallucination filtered: {text!r}")
            return ""

        # Require at least one word (single words like "yes", "ok", "hello" are valid)
        if not text or len(text.strip()) < 2:
            logger.debug(f"Too short, filtered: {text!r}")
            return ""

        logger.debug(f"Transcribed: {text!r}")
        return text

    async def transcribe(self, pcm: bytes) -> str:
        return await asyncio.to_thread(self._do_transcribe, pcm)


whisper_provider = WhisperSTTProvider()
