"""Diarizer provider interface — DS-B owns this."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger("pilot.diarizer")


@dataclass
class DiarSegment:
    speaker_label: str  # spk-0, spk-1 ...
    start: float
    end: float


class DiarizeProvider(ABC):
    @abstractmethod
    async def segment(
        self, pcm: bytes, sample_rate: int = 16000, session_id: str = None
    ) -> list[DiarSegment]: ...


# Global in-memory dictionary for sliding session centroids to track online speakers
_session_centroids: dict[str, list[tuple[str, np.ndarray]]] = {}


def _get_similarity(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


class WeSpeakerEmbedder:
    """Primary speaker-embedding model: WeSpeaker ResNet221-LM (ONNX),
    pretrained on VoxCeleb with large-margin fine-tuning —
    huggingface.co/Wespeaker/wespeaker-voxceleb-resnet221-LM. 256-dim
    embeddings from 80-bin kaldi fbank features + per-utterance CMN, run via
    onnxruntime (already a dependency — same pattern as Smart Turn / Kokoro
    TTS). Deliberately separate from enrollment.py's EmbedProvider, which
    stores users' enrolled voice profiles — this model only ever feeds the
    in-memory, per-session centroids in PyannoteProvider below, so it can be
    swapped without touching identity verification at all.
    """

    MODEL_REPO = "Wespeaker/wespeaker-voxceleb-resnet221-LM"
    MODEL_FILE = "voxceleb_resnet221_LM.onnx"

    def __init__(self):
        self._session = None

    def load(self):
        if self._session is not None:
            return
        try:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download

            from backend.core.config import settings

            cache_dir = Path.home() / ".cache" / "wespeaker"
            model_path = hf_hub_download(
                repo_id=self.MODEL_REPO, filename=self.MODEL_FILE, cache_dir=str(cache_dir)
            )
            # Same device-selection pattern as Kokoro TTS (services/tts.py):
            # CoreML (Apple GPU/ANE) when settings.PREFERRED_DEVICE == "mps"
            # and onnxruntime actually has the provider available, else CPU.
            device_pref = getattr(settings, "PREFERRED_DEVICE", "cpu").lower()
            if device_pref == "mps" and "CoreMLExecutionProvider" in ort.get_available_providers():
                providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
                logger.info("Diarizer: WeSpeaker routing computation to GPU (CoreML MPS) ✓")
            else:
                providers = ["CPUExecutionProvider"]
            self._session = ort.InferenceSession(model_path, providers=providers)
            logger.info("Diarizer: WeSpeaker ResNet221-LM (ONNX) speaker-embedding model loaded")
        except Exception as e:
            logger.warning(f"Diarizer: could not load WeSpeaker ResNet221-LM model — {e}")
            self._session = None

    def extract(self, pcm: bytes, sample_rate: int = 16000) -> "np.ndarray | None":
        if self._session is None:
            return None
        try:
            import torch
            import torchaudio.compliance.kaldi as kaldi

            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if len(audio) < sample_rate // 10:  # shorter than 100ms — too little to embed reliably
                return None
            wav = torch.from_numpy(audio).unsqueeze(0)  # (1, samples)
            # Kaldi-style 80-bin fbank matching the model's training config
            # (voxceleb_resnet221_LM.yaml: num_mel_bins=80, frame_length=25,
            # frame_shift=10); dither=0 for deterministic inference.
            feats = kaldi.fbank(
                wav, num_mel_bins=80, frame_length=25, frame_shift=10,
                dither=0.0, sample_frequency=sample_rate,
            )
            feats = feats - feats.mean(dim=0, keepdim=True)  # per-utterance CMN
            feats_np = feats.unsqueeze(0).numpy().astype(np.float32)  # (1, T, 80)
            (embs,) = self._session.run(["embs"], {"feats": feats_np})
            vec = embs[0].astype(np.float32)
            norm = np.linalg.norm(vec)
            return vec / norm if norm > 0 else vec
        except Exception as e:
            logger.warning(f"Diarizer: WeSpeaker embedding extraction failed — {e}")
            return None


class SpeakerEmbedder:
    """Fallback speaker-embedding model (SpeechBrain ECAPA-TDNN, pretrained
    on VoxCeleb), used only if WeSpeakerEmbedder above fails to load or
    extract — e.g. the ONNX runtime or model download isn't available. Same
    role/isolation from enrollment.py as WeSpeakerEmbedder.
    """

    def __init__(self):
        self._model = None

    def load(self):
        if self._model is not None:
            return
        try:
            import torch  # noqa: F401  (import check — real dependency of speechbrain)
            from speechbrain.inference.speaker import EncoderClassifier

            savedir = Path.home() / ".cache" / "speechbrain" / "spkrec-ecapa-voxceleb"
            self._model = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=str(savedir),
            )
            logger.info("Diarizer: SpeechBrain ECAPA-TDNN speaker-embedding model loaded (fallback)")
        except Exception as e:
            logger.warning(f"Diarizer: could not load SpeechBrain speaker-embedding model, "
                            f"falling back to single-speaker mode — {e}")
            self._model = None

    def extract(self, pcm: bytes, sample_rate: int = 16000) -> "np.ndarray | None":
        if self._model is None:
            return None
        try:
            import torch

            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if len(audio) < sample_rate // 10:  # shorter than 100ms — too little to embed reliably
                return None
            wav = torch.from_numpy(audio).unsqueeze(0)  # (1, samples)
            with torch.no_grad():
                emb = self._model.encode_batch(wav)  # (1, 1, 192)
            vec = emb.squeeze().cpu().numpy().astype(np.float32)
            norm = np.linalg.norm(vec)
            return vec / norm if norm > 0 else vec
        except Exception as e:
            logger.warning(f"Diarizer: embedding extraction failed — {e}")
            return None


class PyannoteProvider(DiarizeProvider):
    """Real online speaker diarization: speaker embeddings (WeSpeaker
    ResNet221-LM primary, SpeechBrain ECAPA-TDNN fallback) + cosine-similarity
    clustering against per-session centroids (below)."""

    def __init__(self):
        self._pipeline = None
        self._primary_embedder = WeSpeakerEmbedder()
        self._fallback_embedder = SpeakerEmbedder()
        self._embedder = None  # set to whichever loaded successfully

    def load(self):
        self._primary_embedder.load()
        if self._primary_embedder._session is not None:
            self._embedder = self._primary_embedder
            logger.info("Pyannote provider: active with WeSpeaker ResNet221-LM online speaker clustering")
            return

        self._fallback_embedder.load()
        if self._fallback_embedder._model is not None:
            self._embedder = self._fallback_embedder
            logger.info("Pyannote provider: WeSpeaker unavailable — active with SpeechBrain "
                        "ECAPA-TDNN online speaker clustering (fallback)")
        else:
            self._embedder = self._fallback_embedder  # extract() returns None → single-speaker
            logger.info("Pyannote provider: no embedding model available — single-speaker fallback")

    async def segment(
        self, pcm: bytes, sample_rate: int = 16000, session_id: str = None
    ) -> list[DiarSegment]:
        duration = len(pcm) / (sample_rate * 2)
        if not session_id:
            return [DiarSegment(speaker_label="spk-0", start=0.0, end=duration)]

        # No real embedding model available (failed to load, or extraction
        # failed for this segment) — fall back to single-speaker rather than
        # cluster on garbage. Never fabricate a speaker split we can't back
        # with a real embedding.
        embedding = self._embedder.extract(pcm, sample_rate)
        if embedding is None:
            return [DiarSegment(speaker_label="spk-0", start=0.0, end=duration)]

        if session_id not in _session_centroids:
            _session_centroids[session_id] = []

        centroids = _session_centroids[session_id]

        best_label = None
        best_score = -1.0

        for label, centroid in centroids:
            score = _get_similarity(embedding, centroid)
            if score > best_score:
                best_score = score
                best_label = label

        # Cosine similarity threshold of 0.72 for matching voice centroids
        if best_score >= 0.72:
            # Update centroid running mean smoothly to adapt to user position/inflection
            for idx, (label, centroid) in enumerate(centroids):
                if label == best_label:
                    updated = 0.8 * centroid + 0.2 * embedding
                    centroids[idx] = (label, updated / np.linalg.norm(updated))
                    break
        else:
            # Register a brand new unique voice cluster for this session context if under the 8-speaker threshold
            if len(centroids) < 8:
                best_label = f"spk-{len(centroids)}"
                centroids.append((best_label, embedding))
                logger.info(
                    f"[diarizer] Registered new session speaker cluster '{best_label}' for session {session_id[:8]} (confidence={best_score:.3f})"
                )
            else:
                # Max speaker threshold of 8 reached: map to the nearest available speaker cluster to prevent cluster overflow
                best_match_label, _ = max(centroids, key=lambda item: _get_similarity(embedding, item[1]))
                best_label = best_match_label
                logger.warning(
                    f"[diarizer] Speaker threshold of 8 reached! Mapping segment to nearest cluster '{best_label}' instead of creating a new cluster."
                )

        return [DiarSegment(speaker_label=best_label, start=0.0, end=duration)]


class SortformerProvider(DiarizeProvider):
    """NVIDIA Streaming Sortformer — DS-B wires real model here."""

    async def segment(
        self, pcm: bytes, sample_rate: int = 16000, session_id: str = None
    ) -> list[DiarSegment]:
        duration = len(pcm) / (sample_rate * 2)
        return [DiarSegment(speaker_label="spk-0", start=0.0, end=duration)]


pyannote_provider = PyannoteProvider()
sortformer_provider = SortformerProvider()
