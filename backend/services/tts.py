"""
TTS — Kokoro ONNX local primary (from capstone_project1_2) → edge-tts → macOS say → espeak-ng.
All output is audio/wav or audio/mp3 — universally supported by browsers.
"""
# postpones the evaluation of type annotations, converting them into plain strings at runtime instead of evaluating them immediately

from __future__ import annotations

import asyncio
import io
import logging
import os
import platform
import subprocess
import tempfile

import numpy as np

from backend.core.config import settings

logger = logging.getLogger("pilot.tts")

_OS = platform.system()

# ── Local Kokoro Initialization ──────────────────────────────────────────────
_kokoro_instance = None
try:
    from kokoro_onnx import Kokoro

    # Check model locations in backend directory
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    model_path = os.path.join(base_dir, "kokoro-v1.0.onnx")
    voices_path = os.path.join(base_dir, "voices-v1.0.bin")
    if os.path.exists(model_path) and os.path.exists(voices_path):
        # onnx : a high-performance engine designed to accelerate machine learning and generative AI models in production.
        import onnxruntime as ort

        # Dynamically allocate CoreML GPU Acceleration or CPU depending on hardware preferences
        device_pref = getattr(settings, "PREFERRED_DEVICE", "cpu").lower()
        if device_pref == "mps" and "CoreMLExecutionProvider" in ort.get_available_providers():
            providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
            logger.info("Local Kokoro ONNX routing computation to GPU (CoreML MPS) ✓")
        else:
            providers = ["CPUExecutionProvider"]
            logger.info(f"Local Kokoro ONNX routing computation to CPU (preferred_device={device_pref}) ✓")

        _kokoro_session = ort.InferenceSession(model_path, providers=providers)
        _kokoro_instance = Kokoro.from_session(_kokoro_session, voices_path)
        logger.info("Local Kokoro ONNX engine initialized successfully!")
    else:
        logger.warning(
            f"Kokoro ONNX files missing from backend folder (checked: {model_path}, {voices_path}). Falling back."
        )
except Exception as e:
    logger.warning(f"Failed to load Kokoro ONNX: {e}")


def _float32_to_int16_bytes(audio: np.ndarray) -> bytes:
    peak = float(np.max(np.abs(audio)))
    if peak > 1e-6:
        audio = audio * (0.95 / peak)
    return np.clip(audio, -1.0, 1.0).astype(np.float32).__mul__(32767).astype(np.int16).tobytes()


async def _kokoro_tts(text: str, speed: float = 1.0) -> bytes:
    if not _kokoro_instance:
        raise RuntimeError("Kokoro not initialized")

    # Generate PCM from local model
    def _gen():
        # sr : sampling rate.
        samples, sr = _kokoro_instance.create(text, voice="af_heart", speed=speed, lang="en-us")
        return _float32_to_int16_bytes(samples), sr

    pcm_bytes, sr = await asyncio.to_thread(_gen)

    # Wrap raw PCM into a standard WAV container
    import wave

    wav_buf = io.BytesIO()
    with wave.open(wav_buf, "wb") as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sr)  # Sample rate (typically 24000)
        wav_file.writeframes(pcm_bytes)

    return wav_buf.getvalue()


_EDGE_VOICES: dict[str, str] = {
    "en": "en-US-AriaNeural",
    "ta": "ta-IN-PallaviNeural",
    "hi": "hi-IN-SwaraNeural",
    "te": "te-IN-ShrutiNeural",
    "fr": "fr-FR-DeniseNeural",
    "es": "es-ES-ElviraNeural",
    "de": "de-DE-KatjaNeural",
    "ja": "ja-JP-NanamiNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ko": "ko-KR-SunHiNeural",
}


def _detect_lang(text: str) -> str:
    try:
        from langdetect import DetectorFactory, detect_langs

        DetectorFactory.seed = 0
        r = detect_langs(text)
        if r and r[0].prob > 0.6:
            return r[0].lang
    except Exception:
        pass
    return "en"


def _speed_str(speed: float) -> str:
    pct = int((speed - 1.0) * 100)
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


#   → Try Kokoro (Tier 1)
#        ↓ fails
#   → Try edge-tts (Tier 2) — also the first choice for non-English
#        ↓ fails
#   → macOS? → say/afconvert (Tier 3)
#   → else  → espeak-ng (Tier 4)
#        ↓ fails
#   → return empty bytes


# ── Tier 2: edge-tts (MP3) (Cloud Default / Multilingual) ───────────────────────
async def _edge_tts(text: str, speed: float) -> bytes:
    import edge_tts

    voice = _EDGE_VOICES.get(_detect_lang(text), "en-US-AriaNeural")
    communicate = edge_tts.Communicate(text, voice, rate=_speed_str(speed))
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    data = buf.getvalue()
    if not data:
        raise RuntimeError("empty")
    return data  # MP3


# ── Tier 3: macOS say → afconvert to WAV (Offline System Fallback) ───────────
def _macos_say_sync(text: str) -> bytes:
    """say outputs AIFF, afconvert converts to WAV (both built-in macOS tools)."""
    aiff = tempfile.mktemp(suffix=".aiff")
    wav = tempfile.mktemp(suffix=".wav")
    try:
        # Generate AIFF
        subprocess.run(
            ["say", "-v", "Samantha", "-r", "185", "-o", aiff, text],
            check=True,
            capture_output=True,
            timeout=15,
        )
        # Convert AIFF → WAV (afconvert is built into macOS)
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16@22050", aiff, wav],
            check=True,
            capture_output=True,
            timeout=10,
        )
        with open(wav, "rb") as f:
            data = f.read()
        if len(data) < 1000:
            raise RuntimeError(f"WAV too small: {len(data)}b")
        logger.info(f"macOS say+afconvert ok: {len(data)}b")
        return data
    finally:
        for p in [aiff, wav]:
            if os.path.exists(p):
                os.unlink(p)


async def _macos_tts(text: str) -> bytes:
    return await asyncio.to_thread(_macos_say_sync, text)


# ── Tier 4: espeak-ng WAV (Linux Offline System Fallback) ──────────────────────
def _espeak_sync(text: str) -> bytes:
    wav = tempfile.mktemp(suffix=".wav")
    try:
        subprocess.run(
            ["espeak-ng", "-v", "en", "-s", "175", "-p", "50", "-w", wav, text],
            check=True,
            capture_output=True,
            timeout=15,
        )
        with open(wav, "rb") as f:
            data = f.read()
        if len(data) < 1000:
            raise RuntimeError(f"espeak too small: {len(data)}b")
        return data
    finally:
        if os.path.exists(wav):
            os.unlink(wav)


async def _espeak_tts(text: str) -> bytes:
    return await asyncio.to_thread(_espeak_sync, text)


def clean_markdown_for_speech(text: str) -> str:
    import re

    # 1. Remove code blocks entirely (spoken responses shouldn't read out raw code blocks)
    text = re.sub(r"```[\s\S]*?```", "", text)
    # 2. Convert markdown links [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # 3. Strip out bold, italic, strikethrough characters: **, *, __, _, ~~
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    text = re.sub(r"~~([^~]+)~~", r"\1", text)
    # 4. Remove inline backticks
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # 5. Remove headers: #, ##, ###, etc.
    text = re.sub(r"^\s*#+\s+", "", text, flags=re.MULTILINE)
    # 6. Remove bullet points or list numbers at the start of a line
    text = re.sub(r"^\s*[-*•+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # 7. Remove blockquote markers: >
    text = re.sub(r"^\s*>\s+", "", text, flags=re.MULTILINE)
    # 8. Clean up extra newlines and whitespaces
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ── Public API ────────────────────────────────────────────────────────────────


# this function converts input string of text into raw audio file bytes. it uses a 4-tier cascading fallback system to insure the application always returns palyback audio, even if individual AI models fails or the host machine goes offline.
async def tts_to_bytes(text: str, speed: float = 1.1) -> tuple[bytes, str]:
    """Always returns audio/wav — supported by all browsers."""
    if not text.strip():
        return b"", "audio/wav"

    cleaned_text = clean_markdown_for_speech(text)
    if not cleaned_text.strip():
        return b"", "audio/wav"

    detected_lang = _detect_lang(cleaned_text)
    logger.info(f"TTS Language Detection: '{detected_lang}' for text '{cleaned_text[:30]}...'")

    # Tier 1: Local Kokoro ONNX (Primary only for English)
    if _kokoro_instance and detected_lang == "en":
        try:
            data = await _kokoro_tts(cleaned_text, speed=1.0)
            logger.info(f"Local Kokoro ONNX ok (en): {len(data)}b")
            return data, "audio/wav"
        except Exception as e:
            logger.warning(f"Local Kokoro ONNX failed ({e}) — trying edge-tts")

    # Tier 2: edge-tts for Multilingual support (Hindi, French, Spanish, Tamil, etc.)
    try:
        data = await _edge_tts(cleaned_text, speed)
        logger.info(f"edge-tts ok ({detected_lang}): {len(data)}b")
        return data, "audio/mp3"
    except Exception as e:
        logger.warning(f"edge-tts failed ({e}) — using system fallback TTS")

    # Tier 3/4: system TTS → WAV
    try:
        if _OS == "Darwin":
            return await _macos_tts(cleaned_text if "cleaned" in locals() else text), "audio/wav"
        else:
            return await _espeak_tts(cleaned_text if "cleaned" in locals() else text), "audio/wav"
    except Exception as e:
        logger.error(f"System fallback TTS failed: {e}")
        return b"", "audio/wav"


# this function is used to stream the audio to the frontend using FastAPI's streaming capability.


async def tts_synthesize(text: str):
    data, _ = await tts_to_bytes(text)
    if data:
        yield data
