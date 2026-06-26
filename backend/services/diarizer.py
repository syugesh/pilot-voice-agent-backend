"""
Diarizer service — pyannote.audio speaker diarization.

Real implementation tries pyannote.audio first.
Fallback: energy-based heuristic (detects speaker changes from silence gaps).

Install pyannote:
    pip install pyannote.audio
    # Requires HuggingFace token: huggingface-cli login
"""
import asyncio
import logging
import numpy as np
from dataclasses import dataclass

logger = logging.getLogger("pilot.diarizer")


@dataclass
class DiarSegment:
    speaker_label: str   # spk-0, spk-1 ...
    start: float
    end: float


class PyannoteProvider:
    """Real pyannote.audio diarization pipeline."""

    def __init__(self):
        self._pipeline = None
        self._loaded = False

    def load(self):
        if self._loaded:
            return
        try:
            from pyannote.audio import Pipeline
            import os
            hf_token = os.environ.get("HF_TOKEN")
            if hf_token:
                self._pipeline = Pipeline.from_pretrained(
                    "pyannote/speaker-diarization-3.1",
                    use_auth_token=hf_token
                )
                logger.info("Pyannote diarizer loaded")
            else:
                logger.warning("HF_TOKEN not set — pyannote not loaded. Set HF_TOKEN in .env")
        except ImportError:
            logger.warning("pyannote.audio not installed — using energy-based fallback")
        except Exception as e:
            logger.warning(f"Pyannote load failed: {e} — using energy fallback")
        finally:
            self._loaded = True

    async def segment(self, pcm: bytes, sample_rate: int = 16000) -> list[DiarSegment]:
        if self._pipeline is not None:
            return await asyncio.to_thread(self._run_pyannote, pcm, sample_rate)
        return _energy_diarize(pcm, sample_rate)

    def _run_pyannote(self, pcm: bytes, sample_rate: int) -> list[DiarSegment]:
        import torch, io, soundfile as sf
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        tensor = torch.from_numpy(audio).unsqueeze(0)
        waveform = {"waveform": tensor, "sample_rate": sample_rate}
        diarization = self._pipeline(waveform)
        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append(DiarSegment(
                speaker_label=f"spk-{speaker.split('_')[-1]}",
                start=turn.start, end=turn.end
            ))
        return segments if segments else [DiarSegment("spk-0", 0.0, len(pcm)/(sample_rate*2))]


def _energy_diarize(pcm: bytes, sample_rate: int = 16000,
                    prev_speaker: str = "spk-0") -> list[DiarSegment]:
    """
    Energy-based speaker classification per turn.
    Returns dominant speaker for the whole segment using RMS profile analysis.
    Uses prev_speaker for continuity when turn follows a short silence.
    """
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if len(audio) == 0:
        return [DiarSegment(prev_speaker, 0.0, 0.0)]

    frame_ms = 20
    frame_size = int(sample_rate * frame_ms / 1000)
    speech_threshold = 0.015
    change_gap_ms = 700  # silence longer than 700ms = potential speaker change
    change_gap_frames = int(change_gap_ms / frame_ms)
    min_speech_frames = int(200 / frame_ms)  # need 200ms of speech before new speaker

    frames = [audio[i:i+frame_size] for i in range(0, len(audio)-frame_size, frame_size)]
    if not frames:
        return [DiarSegment(prev_speaker, 0.0, len(audio) / sample_rate)]

    energies = [float(np.sqrt(np.mean(f**2 + 1e-10))) for f in frames]
    is_speech = [e > speech_threshold for e in energies]

    duration = len(frames) * frame_ms / 1000
    speech_count = sum(is_speech)

    if speech_count < min_speech_frames:
        return [DiarSegment(prev_speaker, 0.0, duration)]

    segments: list[DiarSegment] = []
    current_speaker = prev_speaker
    speaker_idx = int(prev_speaker.split("-")[-1]) if prev_speaker.startswith("spk-") else 0
    seg_start = 0.0
    silence_count = 0
    pending_speech = 0

    for i, speech in enumerate(is_speech):
        t = i * frame_ms / 1000
        if not speech:
            silence_count += 1
            pending_speech = 0
            if silence_count == change_gap_frames:
                segments.append(DiarSegment(current_speaker, seg_start, t))
                speaker_idx = (speaker_idx + 1) % 2
                current_speaker = f"spk-{speaker_idx}"
                seg_start = t
        else:
            silence_count = 0
            pending_speech += 1

    segments.append(DiarSegment(current_speaker, seg_start, duration))
    return segments if segments else [DiarSegment(prev_speaker, 0.0, duration)]


# Singleton
pyannote_provider = PyannoteProvider()
