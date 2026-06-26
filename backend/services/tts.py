"""
TTS service for PILOT — adapted from the multilingual TTS reference implementation.

Engine priority
───────────────
1. edge-tts  (primary)  – Microsoft Edge neural voices, 40+ languages including
                          Tamil, Hindi and other Indian languages. Needs internet.
2. Kokoro-ONNX (fallback) – fully offline, English-only. Models auto-downloaded
                             to ~/.cache/kokoro/ on first use.
3. macOS say / espeak-ng  – last resort system TTS.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import platform
import subprocess
import tempfile
import urllib.request
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger("pilot.tts")
_OS = platform.system()   # "Darwin" | "Linux"

# ── Edge-TTS voice map ────────────────────────────────────────────────────────
_EDGE_VOICES: dict[str, str] = {
    "en": "en-US-AriaNeural",
    "ta": "ta-IN-PallaviNeural",
    "hi": "hi-IN-SwaraNeural",
    "te": "te-IN-ShrutiNeural",
    "kn": "kn-IN-SapnaNeural",
    "ml": "ml-IN-SobhanaNeural",
    "bn": "bn-IN-TanishaaNeural",
    "gu": "gu-IN-DhwaniNeural",
    "mr": "mr-IN-AarohiNeural",
    "ur": "ur-PK-UzmaNeural",
    "fr": "fr-FR-DeniseNeural",
    "es": "es-ES-ElviraNeural",
    "de": "de-DE-KatjaNeural",
    "ja": "ja-JP-NanamiNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ko": "ko-KR-SunHiNeural",
    "ar": "ar-SA-ZariyahNeural",
    "pt": "pt-BR-FranciscaNeural",
    "it": "it-IT-ElsaNeural",
    "ru": "ru-RU-SvetlanaNeural",
}
_DEFAULT_VOICE = "en-US-AriaNeural"


# ── Language detection ─────────────────────────────────────────────────────────
def _detect_lang(text: str) -> str:
    try:
        from langdetect import detect_langs, DetectorFactory
        DetectorFactory.seed = 0
        results = detect_langs(text)
        if not results:
            return "en"
        top = results[0]
        if top.prob > 0.6:
            return top.lang
        # Short ASCII text is almost always English
        ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
        if ascii_ratio > 0.9:
            return "en"
        return top.lang
    except Exception:
        return "en"


def _speed_str(speed: float) -> str:
    pct = int((speed - 1.0) * 100)
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


# ── Tier 1: edge-tts ──────────────────────────────────────────────────────────
async def _edge_tts(text: str, speed: float) -> tuple[bytes, str]:
    """Returns (mp3_bytes, 'audio/mp3')."""
    import edge_tts
    lang  = _detect_lang(text)
    voice = _EDGE_VOICES.get(lang, _DEFAULT_VOICE)
    logger.debug(f"edge-tts: lang={lang} voice={voice}")

    communicate = edge_tts.Communicate(text, voice, rate=_speed_str(speed))
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    data = buf.getvalue()
    if not data:
        raise RuntimeError("edge-tts returned empty audio")
    return data, "audio/mp3"


# ── Tier 2: Kokoro-ONNX (offline fallback) ───────────────────────────────────
_KOKORO_CACHE = Path.home() / ".cache" / "kokoro"
_KOKORO_BASE  = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
_KOKORO_MODEL  = "kokoro-v1.0.onnx"
_KOKORO_VOICES = "voices-v1.0.bin"


def _ensure_kokoro_file(filename: str) -> str:
    _KOKORO_CACHE.mkdir(parents=True, exist_ok=True)
    dest = _KOKORO_CACHE / filename
    if not dest.exists():
        url = f"{_KOKORO_BASE}/{filename}"
        logger.info(f"Downloading Kokoro model: {filename} …")
        urllib.request.urlretrieve(url, dest)
        logger.info(f"Kokoro {filename} downloaded ({dest.stat().st_size // 1024} KB)")
    return str(dest)


@lru_cache(maxsize=1)
def _load_kokoro():
    from kokoro_onnx import Kokoro
    logger.info("Loading Kokoro-ONNX (offline TTS) …")
    model  = _ensure_kokoro_file(_KOKORO_MODEL)
    voices = _ensure_kokoro_file(_KOKORO_VOICES)
    kokoro = Kokoro(model, voices)
    logger.info("Kokoro-ONNX loaded ✓")
    return kokoro


def _kokoro_sync(text: str, speed: float) -> tuple[bytes, str]:
    kokoro = _load_kokoro()
    samples, sr = kokoro.create(text, voice="af_heart", speed=speed, lang="en-us")
    buf = io.BytesIO()
    sf.write(buf, samples.astype(np.float32), sr, format="WAV")
    wav = buf.getvalue()
    if len(wav) < 500:
        raise RuntimeError("Kokoro WAV too small")
    return wav, "audio/wav"


async def _kokoro_tts(text: str, speed: float) -> tuple[bytes, str]:
    return await asyncio.to_thread(_kokoro_sync, text, speed)


# ── Tier 3: macOS say / espeak-ng ─────────────────────────────────────────────
def _macos_say_sync(text: str) -> bytes:
    aiff_fd, aiff = tempfile.mkstemp(suffix=".aiff")
    wav_fd,  wav  = tempfile.mkstemp(suffix=".wav")
    os.close(aiff_fd); os.close(wav_fd)
    try:
        subprocess.run(["say", "-v", "Samantha", "-r", "185", "-o", aiff, text],
                       check=True, capture_output=True, timeout=15)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@22050", aiff, wav],
                       check=True, capture_output=True, timeout=10)
        with open(wav, "rb") as f:
            data = f.read()
        if len(data) < 1000:
            raise RuntimeError(f"WAV too small: {len(data)}b")
        return data
    finally:
        for p in [aiff, wav]:
            if os.path.exists(p):
                os.unlink(p)


def _espeak_sync(text: str) -> bytes:
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        subprocess.run(["espeak-ng", "-v", "en", "-s", "175", "-w", wav, text],
                       check=True, capture_output=True, timeout=15)
        with open(wav, "rb") as f:
            data = f.read()
        if len(data) < 1000:
            raise RuntimeError(f"espeak too small: {len(data)}b")
        return data
    finally:
        if os.path.exists(wav):
            os.unlink(wav)


async def _system_tts(text: str) -> tuple[bytes, str]:
    if _OS == "Darwin":
        return await asyncio.to_thread(_macos_say_sync, text), "audio/wav"
    return await asyncio.to_thread(_espeak_sync, text), "audio/wav"


# ── Public API ────────────────────────────────────────────────────────────────
async def tts_to_bytes(text: str, speed: float = 1.0) -> tuple[bytes, str]:
    """
    Returns (audio_bytes, mime_type) for WebSocket streaming to the browser.

    Priority:
      1. edge-tts  → audio/mp3  (multilingual, needs internet)
      2. Kokoro    → audio/wav  (English-only, offline, auto-downloads models)
      3. system    → audio/wav  (macOS say / espeak-ng)
    """
    if not text.strip():
        return b"", "audio/wav"

    # Tier 1: edge-tts
    try:
        data, mime = await _edge_tts(text, speed)
        logger.info(f"edge-tts ok: {len(data)}b {mime}")
        return data, mime
    except Exception as e:
        logger.warning(f"edge-tts failed ({e}) — trying Kokoro offline")

    # Tier 2: Kokoro (offline)
    try:
        data, mime = await _kokoro_tts(text, speed)
        logger.info(f"Kokoro TTS ok: {len(data)}b")
        return data, mime
    except Exception as e:
        logger.warning(f"Kokoro failed ({e}) — using system TTS")

    # Tier 3: system TTS
    try:
        data, mime = await _system_tts(text)
        logger.info(f"System TTS ok: {len(data)}b")
        return data, mime
    except Exception as e:
        logger.error(f"All TTS providers failed: {e}")
        return b"", "audio/wav"


async def tts_synthesize(text: str):
    data, _ = await tts_to_bytes(text)
    if data:
        yield data
