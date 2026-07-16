import asyncio
import logging
import threading
from pathlib import Path

import numpy as np

from backend.core.config import settings

logger = logging.getLogger("pilot.stt")

# Whisper hallucinates these on silence — filter them out
_HALLUCINATIONS = {
    "thank you",
    "thanks for watching",
    "thanks for listening",
    "bye",
    "goodbye",
    "see you",
    "you",
    ".",
    "..",
    "...",
    "thanks",
    "thank you.",
    "thank you for watching.",
    "thanks for watching.",
    "please subscribe",
    "subtitles by",
    "transcribed by",
    "[music]",
    "[applause]",
    "[blank_audio]",
    "[ Silence ]",
    "silence",
}

# Legitimate one-word voice commands that must never be dropped
# by the short-transcript filter below.
_SHORT_COMMANDS = {
    "yes",
    "no",
    "stop",
    "cancel",
    "pause",
    "resume",
    "play",
    "next",
    "back",
    "help",
    "ok",
    "okay",
    "go",
    "wait",
    "confirm",
    "deny",
    "exit",
    "quit",
    "skip",
    "repeat",
}

_FALLBACKS = ["distil-large-v3", "small", "base"]


class WhisperSTTProvider:
    def __init__(self):
        self._model = None
        self._model_lock = threading.Lock()
        self._use_mlx = False
        self._mlx_model_path = "mlx-community/whisper-large-v3"

    # Maps your configured WHISPER_MODEL setting (e.g. "large-v3-turbo", "distil-large-v3", "small")
    # to the correct MLX-community HuggingFace repo string.
    # Falls back to the turbo-q4 model if nothing matches.
    def _get_hf_mlx_path(self, settings) -> str:
        preferred = getattr(settings, "WHISPER_MODEL", "large-v3-turbo")

        if "large-v3-turbo" in preferred or ("turbo" in preferred and "large" in preferred):
            return "mlx-community/whisper-large-v3-turbo-q4"
        elif "distil-large-v3" in preferred:
            # No quantized ("-q4"/"-4bit"/"-8bit") build of distil-whisper-large-v3
            # exists on mlx-community (verified against the HF API) — the
            # "-turbo-q4" suffix here previously pointed at a repo that was
            # never real, silently failing every load and falling back to a
            # slower path. This is the actual, existing repo (fp16, but still
            # far faster than full large-v3 thanks to distil's 2-layer
            # decoder vs. large-v3's 32).
            return "mlx-community/distil-whisper-large-v3"
        elif preferred in ["medium", "small", "base", "tiny"]:
            return f"mlx-community/whisper-{preferred}-mlx-4bit"
        else:
            return "mlx-community/whisper-large-v3"

    # A small static helper that converts a HF repo id like
    # mlx-community/whisper-large-v3-turbo-q4 into the actual local cache folder
    # name HuggingFace uses (models--mlx-community--whisper-large-v3-turbo-q4).
    # This replaces a bug in the old version where the cache check was hardcoded
    # to look for whisper-large-v3-mlx regardless of what model was actually preferred —
    # now it checks the cache for the correct preferred model.
    @staticmethod
    def _hf_cache_dir_for(repo_id: str) -> Path:
        folder = "models--" + repo_id.replace("/", "--")
        return Path.home() / ".cache" / "huggingface" / "hub" / folder

    # Tries to import mlx_whisper.
    # If available, sets _use_mlx = True and picks a model path in priority order:
    # cached HF model → local on-disk MLX model → fresh HF download path.
    #
    # If MLX isn't available at all (e.g. not on Apple Silicon),
    # falls back to loading a faster-whisper CPU model immediately,
    # trying your preferred model then the fallback list
    # (distil-large-v3 → small → base).
    def load(self):
        # 1. Try to load MLX Whisper first (high performance on Apple Silicon)
        try:
            import mlx_whisper  # type: ignore

            self._use_mlx = True

            preferred_repo = self._get_hf_mlx_path(settings)
            cache_path = self._hf_cache_dir_for(preferred_repo)
            local_mlx = (
                Path(__file__).resolve().parent.parent / "whisper-large-v3"
            )

            if cache_path.exists():
                self._mlx_model_path = preferred_repo
                logger.info(
                    f"Priority 1: Cached MLX Whisper model detected: {self._mlx_model_path}"
                )
            elif local_mlx.exists():
                self._mlx_model_path = str(local_mlx)
                logger.info(
                    f"Priority 2: Local MLX Whisper model detected: {self._mlx_model_path}"
                )
            else:
                self._mlx_model_path = preferred_repo
                logger.info(
                    f"Priority 3: Defaulting to Hugging Face MLX path: {self._mlx_model_path}"
                )
            return

        except Exception as mlx_err:
            logger.info(
                f"MLX Whisper load skipped/failed ({mlx_err}). Falling back to faster-whisper CPU."
            )
            self._use_mlx = False

        # 2. Fall back to faster-whisper CPU
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

    # Use case — live mic audio chunk arrives every ~500ms from PILOT's audio capture loop:
    def _do_transcribe(self, pcm: bytes) -> str:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

        if len(audio) < 3200:  # < 200ms — skip
            return ""

        # Check audio energy — reject silent segments
        rms = float((audio**2).mean() ** 0.5)
        if rms < 0.005:  # essentially silence
            return ""

        language = getattr(settings, "WHISPER_LANGUAGE", "en")

        # 1. MLX Transcribe (Apple Silicon GPU)
        if self._use_mlx:
            try:
                import mlx_whisper  # type: ignore

                result = mlx_whisper.transcribe(
                    audio,
                    path_or_hf_repo=self._mlx_model_path,
                    language=language,
                    no_speech_threshold=0.6,
                    compression_ratio_threshold=2.4,
                    condition_on_previous_text=False,
                )
                text = result.get("text", "").strip()

            except Exception as e:
                logger.error(
                    f"MLX Whisper transcription failed with {self._mlx_model_path}, trying fallback: {e}"
                )

                local_mlx = str(
                    Path(__file__).resolve().parent.parent
                    / "whisper-large-v3-turbo-4bit"
                )

                if self._mlx_model_path == local_mlx:
                    try:
                        hf_path = self._get_hf_mlx_path(settings)
                        logger.info(
                            f"Attempting fallback to Hugging Face MLX model: {hf_path}"
                        )

                        result = mlx_whisper.transcribe(
                            audio,
                            path_or_hf_repo=hf_path,
                            language=language,
                            no_speech_threshold=0.6,
                            compression_ratio_threshold=2.4,
                            condition_on_previous_text=False,
                        )
                        text = result.get("text", "").strip()
                        self._mlx_model_path = hf_path  # switch path for next runs

                    except Exception as hf_err:
                        logger.error(f"HF MLX fallback also failed: {hf_err}")
                        text = self._transcribe_faster_whisper_fallback(
                            audio, language
                        )
                else:
                    text = self._transcribe_faster_whisper_fallback(audio, language)

        # 2. faster-whisper Transcribe (CPU)
        else:
            text = self._transcribe_faster_whisper_fallback(audio, language)

        # Filter hallucinations
        normalized = text.lower().strip(" .")  # abbreviations , lower case , digit conversion.
        if normalized in _HALLUCINATIONS:
            logger.debug(f"Hallucination filtered: {text!r}")
            return ""

        # Filter very short transcripts (likely noise) — but never drop
        # legitimate one-word voice commands.
        if len(text.split()) < 2 and normalized not in _SHORT_COMMANDS:
            logger.debug(f"Too short, filtered: {text!r}")
            return ""

        return text

    # Runs the actual faster-whisper inference with beam_size=1,
    # no_speech_threshold=0.6, compression_ratio_threshold=2.4,
    # and condition_on_previous_text=False
    # (this last one prevents Whisper from hallucinating based on prior context).
    #
    # Once a CPU model is loaded, this is the literal "ask the model" step:
    # audio of "turn on the lights" → returns "Turn on the lights."
    # as a plain string, joining whatever segments faster-whisper split it into.
    def _transcribe_faster_whisper(self, audio: np.ndarray, language: str) -> str:
        segments, info = self._model.transcribe(
            audio,
            beam_size=1,
            language=language,
            vad_filter=False,
            no_speech_threshold=0.6,  # reject low-confidence speech
            compression_ratio_threshold=2.4,
            condition_on_previous_text=False,  # prevent context contamination
        )
        return " ".join(segment.text for segment in segments).strip()

    # The race condition this fixes:
    # Two audio chunks arrive almost simultaneously and both get dispatched to
    # asyncio.to_thread, landing on two different worker threads.
    # Both call this fallback at the same moment because self._model is None
    # (CPU model never loaded yet, since you're on MLX-first Mac and the local model just failed).
    def _transcribe_faster_whisper_fallback(
        self, audio: np.ndarray, language: str
    ) -> str:
        if self._model is None:
            with self._model_lock:
                # Re-check inside the lock — another thread may have
                # already loaded the model while we were waiting.
                if self._model is None:
                    logger.info("Initializing faster-whisper CPU fallback on-demand...")

                    preferred = getattr(settings, "WHISPER_MODEL", "distil-large-v3")
                    order = [preferred] + [m for m in _FALLBACKS if m != preferred]

                    for name in order:
                        try:
                            from faster_whisper import WhisperModel

                            self._model = WhisperModel(
                                name, device="cpu", compute_type="int8"
                            )
                            logger.info(f"Whisper loaded: {name}")
                            break
                        except Exception as e:
                            logger.warning(
                                f"Whisper {name} failed: {e} — trying next"
                            )

        if self._model is None:
            return ""

        return self._transcribe_faster_whisper(audio, language)

    # The public API for the rest of PILOT.
    # It wraps your synchronous _do_transcribe in asyncio.to_thread
    # so that even if _do_transcribe blocks for a moment on MLX or
    # faster-whisper inference, the asyncio event loop stays responsive
    # and the server doesn't freeze.
    async def transcribe(self, pcm: bytes) -> str:
        return await asyncio.to_thread(self._do_transcribe, pcm)


whisper_provider = WhisperSTTProvider()