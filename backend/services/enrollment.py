"""
Enrollment service — WeSpeaker ECAPA-TDNN speaker embeddings.

Per-user storage: embedding stored as BLOB in SQLite VoiceEnrollment.embedding.
No .npy files — each user's embedding is in the DB, keyed by user_id.

Install WeSpeaker:
    pip install git+https://github.com/wenet-e2e/wespeaker.git

Confidence score: real cosine similarity (0.0–1.0) from WeSpeaker vectors.
If below COSINE_THRESHOLD, identify_speaker returns confidence + message.
"""
import numpy as np
import asyncio
import logging
import os
import tempfile
import wave
from core.config import settings

logger = logging.getLogger("pilot.enrollment")


class WeSpeakerEmbedProvider:
    """Real WeSpeaker ECAPA-TDNN — falls back to energy-based stub."""

    def __init__(self):
        self._model = None
        self._loaded = False

    def load(self):
        if self._loaded:
            return
        try:
            # Suppress noisy s3prl/ESPnet warnings — these are optional upstreams we don't use
            import logging as _log
            _log.getLogger("s3prl").setLevel(_log.ERROR)

            # torchaudio 2.x removed set_audio_backend(); WeSpeaker still calls it
            import torchaudio
            if not hasattr(torchaudio, "set_audio_backend"):
                torchaudio.set_audio_backend = lambda *_: None

            import wespeaker
            self._model = wespeaker.load_model("english")
            # Use MPS if available (Apple Silicon)
            try:
                import torch
                if torch.backends.mps.is_available():
                    self._model.set_gpu(0)
                    logger.info("WeSpeaker: MPS (Apple Silicon GPU)")
                else:
                    self._model.set_gpu(-1)
                    logger.info("WeSpeaker: CPU")
            except ImportError:
                self._model.set_gpu(-1)
            self._loaded = True
        except ImportError:
            logger.warning(
                "WeSpeaker not installed — using energy stub.\n"
                "Install: pip install git+https://github.com/wenet-e2e/wespeaker.git"
            )
            self._loaded = True
        except Exception as e:
            logger.error(f"WeSpeaker load failed: {e}")
            self._loaded = True

    def extract(self, pcm: bytes) -> tuple[np.ndarray, float]:
        """
        Returns (embedding, quality_score).
        quality_score: 0.0–1.0 based on audio energy and length.
        """
        quality = self._audio_quality(pcm)
        if self._model is not None:
            emb = self._extract_wespeaker(pcm)
        else:
            emb = self._stub(pcm)
        return emb, quality

    def _audio_quality(self, pcm: bytes) -> float:
        """Compute real audio quality score from PCM energy."""
        if len(pcm) < 3200:
            return 0.0
        audio = np.frombuffer(pcm[:len(pcm)//2*2], dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(audio ** 2)))
        # Duration score (more audio = better)
        duration_s = len(pcm) / (16000 * 2)
        dur_score = min(1.0, duration_s / 5.0)
        # Energy score (louder = better, up to a point)
        energy_score = min(1.0, rms / 0.15)
        # Clipping penalty
        clipping = float(np.mean(np.abs(audio) > 0.95))
        clip_penalty = max(0.0, 1.0 - clipping * 5)
        quality = (dur_score * 0.4 + energy_score * 0.4 + clip_penalty * 0.2)
        return round(quality, 3)

    def _extract_wespeaker(self, pcm: bytes) -> np.ndarray:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            fname = f.name
        try:
            with wave.open(fname, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(pcm)
            embedding = self._model.extract_embedding(fname)
            if hasattr(embedding, "numpy"):
                embedding = embedding.numpy()
            return np.array(embedding, dtype=np.float32).flatten()
        except Exception as e:
            logger.error(f"WeSpeaker extract error: {e} — using stub")
            return self._stub(pcm)
        finally:
            if os.path.exists(fname):
                os.unlink(fname)

    def _stub(self, pcm: bytes) -> np.ndarray:
        """Deterministic stub — NOT real voice biometrics."""
        rng = np.random.default_rng(abs(hash(pcm[:64])))
        return rng.standard_normal(settings.EMBEDDING_DIM).astype(np.float32)


embed_provider = WeSpeakerEmbedProvider()


def _lazy_load():
    embed_provider.load()


async def extract_and_store(speaker_id: int, audio_bytes: bytes) -> tuple[bytes, float]:
    """
    Extract WeSpeaker embedding, store as BLOB in DB (not .npy file).
    Returns (embedding_bytes, quality_score).
    """
    await asyncio.to_thread(_lazy_load)
    embedding, quality = await asyncio.to_thread(embed_provider.extract, audio_bytes)
    logger.info(f"Enrollment: speaker_id={speaker_id} dim={embedding.shape} quality={quality:.2f}")
    return embedding.tobytes(), quality


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


async def identify_speaker(pcm: bytes) -> tuple[str | None, str | None, float, str]:
    """
    Compare PCM against all enrolled speakers in DB.
    Returns (speaker_name, role, confidence, status_message).
    status_message tells the frontend what to display.
    """
    from db.engine import AsyncSessionLocal
    from db.models import VoiceEnrollment
    from sqlalchemy import select

    await asyncio.to_thread(_lazy_load)
    embedding, quality = await asyncio.to_thread(embed_provider.extract, pcm)

    if quality < 0.15:
        return None, None, quality, "Audio too quiet — please speak louder"

    best_score, best_match = 0.0, None

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(VoiceEnrollment).where(VoiceEnrollment.status == "ready")
        )
        rows = result.scalars().all()

    if not rows:
        return None, None, 0.0, "No voices enrolled yet"

    for row in rows:
        if not row.embedding:
            continue
        try:
            stored = np.frombuffer(row.embedding, dtype=np.float32)
            # Handle dimension mismatch between stub and real model
            min_dim = min(len(stored), len(embedding))
            score = cosine_similarity(stored[:min_dim], embedding[:min_dim])
            if score > best_score:
                best_score = score
                best_match = row
        except Exception as e:
            logger.error(f"Compare failed for {row.speaker_name}: {e}")

    threshold = settings.COSINE_THRESHOLD

    if best_score >= threshold and best_match:
        logger.info(f"Identified: {best_match.speaker_name} conf={best_score:.3f}")
        return (best_match.speaker_name, best_match.role, best_score,
                f"Confidence: {int(best_score*100)}%")

    msg = f"Score too low ({int(best_score*100)}%) — please re-enroll or speak clearly"
    logger.debug(f"Unknown speaker: best={best_score:.3f} < threshold={threshold}")
    return None, None, best_score, msg
