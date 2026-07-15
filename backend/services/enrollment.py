"""
Enrollment service — audio → WeSpeaker embedding + cosine identity.
DS-A owns enrollment.py (enrollment + cosine ID).
DS-B owns diarizer.py (diarizer provider).
"""





import logging
import os

import numpy as np

from backend.core.config import settings

logger = logging.getLogger("pilot.enrollment")


def _decode_to_raw_pcm(pcm: bytes) -> bytes:
    """Shared container-decoding step (webm/wav → raw 16kHz mono int16 PCM),
    used by both the real-model path and the legacy fallback path below."""
    if pcm.startswith(b"RIFF") or pcm.startswith(b"\x1a\x45\xdf\xa3") or (len(pcm) % 2 != 0):
        try:
            import io

            from pydub import AudioSegment  # type:ignore

            AudioSegment.converter = "/opt/homebrew/bin/ffmpeg"
            AudioSegment.ffprobe = "/opt/homebrew/bin/ffprobe"

            bio = io.BytesIO(pcm)
            seg = AudioSegment.from_file(bio)
            seg = seg.set_frame_rate(16000).set_channels(1)
            pcm = seg.raw_data
        except Exception as ex:
            logger.warning(f"Could not decode audio container using pydub: {ex}")

    if len(pcm) % 2 != 0:
        pcm = pcm[:-1]
    return pcm


class EmbedProvider:
    """Acoustic voice embedding extractor for enrollment + identification.

    Uses the real SpeechBrain ECAPA-TDNN speaker-embedding model (the same
    one backend/services/diarizer.py uses for in-session clustering) when
    it's available — this is a genuine, trained biometric embedding, so
    cosine similarity between an enrollment sample and a later live-session
    sample of the same person is actually meaningful.

    Falls back to a hand-rolled pitch/Mel-spectrum feature extractor ONLY if
    torch/speechbrain aren't installed in this environment, so voice ID
    doesn't hard-crash without those deps. That fallback is NOT a reliable
    biometric match: its 512-dim projection is derived from a random matrix
    seeded by the utterance's own content, so two different recordings of
    the same person land in unrelated projected spaces almost every time —
    this was the actual root cause of genuinely-enrolled speakers other than
    the primary user failing to be recognized in a live session. Kept only
    as a last-resort fallback, never as the intended path.
    """

    def __init__(self):
        self._real_embedder = None  # lazy: backend.services.diarizer.SpeakerEmbedder

    def _get_real_embedder(self):
        if self._real_embedder is None:
            from backend.services.diarizer import SpeakerEmbedder
            self._real_embedder = SpeakerEmbedder()
            self._real_embedder.load()
        return self._real_embedder

    def extract(self, pcm: bytes) -> np.ndarray:
        raw_pcm = _decode_to_raw_pcm(pcm)

        real_embedder = self._get_real_embedder()
        real_vec = real_embedder.extract(raw_pcm, sample_rate=16000)
        if real_vec is not None:
            return real_vec

        logger.warning(
            "EmbedProvider: real speaker-embedding model unavailable — using the "
            "legacy hand-rolled fallback, which is NOT a reliable biometric match."
        )
        return self._extract_legacy_unreliable(raw_pcm)

    def _extract_legacy_unreliable(self, pcm: bytes) -> np.ndarray:
        """Original hand-rolled pitch/Mel-spectrum + random-projection
        extractor — kept only as a last resort when the real model can't
        load. See the EmbedProvider docstring for why this is unreliable."""

        # Convert raw PCM bytes (Int16) to float32 normalized between -1.0 and 1.0
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if len(audio) == 0:
            return np.zeros(512, dtype=np.float32)

        # 1. Compute Short-Time Fourier Transform (STFT)
        # Slicing the audio into window frames and averaging power spectra
        frame_size = 512  # window size (number of audio samples analyzed in a single snapshot.) (512/16000 = 32ms )
        hop_size = 256   #samples (16ms step , creating a 50% overlap to oprevent temporal data loss.) 
        n_fft = 512  # number of fast fourier tnrasform bins (size of the fft window to convert time domain to frequency domain.)

        # Hann window
        # smooth bell curve.
        #a mathematical function used in digital signal processing to smoothly taper the edges of a finite signal. By forcing a signal's endpoints to seamlessly taper to zero, it reduces "spectral leakage" during Fourier transforms (FFT/DFT), which improves frequency resolution and analysis dynamic range.Hanning window to audio signals helps in reducing spectral leakage, which can obscure or distort the frequency components of a sound.

        

        window = 0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(frame_size) / (frame_size - 1)))

        # Slice and compute real FFT power spectrum
        spectra = []
        for i in range(0, len(audio) - frame_size, hop_size):
            frame = audio[i : i + frame_size] * window
            fft_res = np.abs(np.fft.rfft(frame, n=n_fft))
            spectra.append(fft_res)

        if not spectra:
            # Fallback for short segments
            fft_res = np.abs(np.fft.rfft(audio, n=n_fft))
            spectra = [fft_res]

        power_spectrum = np.mean(spectra, axis=0)

        # 2. Map to Mel-scale Filterbanks (40 Mel filters) triangular filters (distributed linearly on the Mel scale between 0 and 8000Hz)
        # mel scale which mimics how the human ear actually perceives pitch. 
        # human hearing is non linear.
        n_mels = 40
        mel_min = 0.0
        max_mel = 2595.0 * np.log10(1.0 + 8000.0 / 700.0)
        mel_points = np.linspace(mel_min, max_mel, n_mels + 2)
        hz_points = 700.0 * (
            10.0 ** (mel_points / 2595.0) - 1.0
        )  # converting the mel points into hertz scale.
        bins = np.floor((n_fft + 1) * hz_points / 16000.0).astype(
            int
        )  # tranlate the wraped hertz points into indices of the fft power spectrum .

        # traigular shape generation.
        # shape of the triangular filter fb[m-1](filter starting point),fb[m](peak),fb[m+1](filter ending point)


        # filterbank generation. 
        fb = np.zeros((n_mels, n_fft // 2 + 1))
        for m in range(1, n_mels + 1):
            # calculate the rising slope of the traingle.
            for k in range(bins[m - 1], bins[m]):
                fb[m - 1, k] = (k - bins[m - 1]) / (bins[m] - bins[m - 1])
            # calculate the falling slope of the traingle.
            for k in range(bins[m], bins[m + 1]):
                fb[m - 1, k] = (bins[m + 1] - k) / (bins[m + 1] - bins[m])

        # apply the filter on the power spectrum to get the mel energies.
        mel_energies = np.dot(fb, power_spectrum)

        # Log compression / just like max pooling or min pooling. to replicate the logarithmic way the human ear perceives changes in volume (decibles)
        log_mel = np.log(mel_energies + 1e-6)

        # 3. Extract pitch (autocorrelation)
        audio_norm = audio - np.mean(audio)  # remove the DC offset(any frequency hum or silent bias.)

        # compute how much a sound wave correlates with a delayed copy of itself over varyinh lags. 
        #How it works: Unlike standard correlation (which compares two different variables like X and Y), autocorrelation compares a single variable with itself at previous time steps. 
        # The Values: Autocorrelation ranges from -1 to +1:
        # +1 (Positive Autocorrelation): High values are typically followed by other high values (trend persistence).
        # -1 (Negative Autocorrelation): High values are typically followed by low values (mean reversion).
        # 0: No relationship; observations are completely independent of past values. 
        corr = np.correlate(
            audio_norm, audio_norm, mode="full"
        )  # autocorrelation (how a signal relates to a delayed copy of iteself.)
        corr = corr[len(corr) // 2 :]  # remove the negative side of the autocorrelation

        # f0-range : restrict the search range to lags between 40 to 60 hertz because human vocal pitch operates between 40-60 Hz.
        # 16000/40 = 40
        # 16000/266 ~ 60;
        pitch_val = 0.0
        if len(corr) > 266:
            f0_range = corr[40:266]
            if len(f0_range) > 0:
                # finds the delay(lag) that has the highest correlation peak.
                pitch_val = float(np.argmax(f0_range) + 40) / 16000.0

        # 4. Create deterministic projection to 512 dimensions
        features = np.zeros(
            512, dtype=np.float32
        )  # receives the 40 log mel energy values (quality of speech).
        features[:40] = log_mel  # receives the fundamental voice pitch value
        features[40] = pitch_val  # padding filled with zeros to match the required 512 dim size.

        # Compute spectral centroid & bandwidth
        freqs = np.linspace(
            0, 8000, len(power_spectrum)
        )  # create a range of frequencies from 0 to 8000 with the same number of values as the power spectrum.
        centroid = np.sum(freqs * power_spectrum) / (np.sum(power_spectrum) + 1e-6)
        bandwidth = np.sum(np.abs(freqs - centroid) * power_spectrum) / (
            np.sum(power_spectrum) + 1e-6
        )  # measures the spread of the frequencies around the centorid .
        features[41] = centroid / 8000.0  # normalize the centroid to the range of 0-1
        features[42] = bandwidth / 8000.0  # normalize the bandwidth to the range of 0-1

        # now we have exactly 43 extracted features. now we are going to project these 43 features into 512 dimensional feature vector.
        # we will use numpy's random number generator to generate a random projection matrix of size 43x512.
        # then we will multiply the features vector with the projection matrix to get the 512 dimensional feature vector.

        # Deterministically project features to 512-dimensional embedding space
        seed_key = (
            int(abs(np.sum(features[:43]) * 100000)) % 9999999
        )  # computes a unique seed based on the exact sum of the 43 extracted features . # jab bhi random number generate hoga same hi hoga in each case.
        rng = np.random.default_rng(seed_key)
        # generates a 512-lement array following a normal distribution. this spreads normal distributions .this spreads out structured 43 features across a 512 dim vector space .
        projection = rng.standard_normal(512).astype(np.float32)

        # integrates the average log-mel energy and the spectral centroid into the 512 dim projection. this ensures the output vector reflects both the speaker's voice frequencies and its physical charactristics.
        final_vector = projection * (np.sum(log_mel) / 40.0) + features[41] * 0.1

        # L2 normalize
        norm = np.linalg.norm(final_vector)
        if norm > 0:
            final_vector = final_vector / norm

        return final_vector


embed_provider = EmbedProvider()


async def extract_and_store(speaker_id: int, audio_bytes: bytes) -> tuple[bytes, str]:
    embedding = await __import__("asyncio").to_thread(embed_provider.extract, audio_bytes)
    os.makedirs("data/embeddings", exist_ok=True)
    path = f"data/embeddings/speaker_{speaker_id}.npy"
    np.save(path, embedding)
    return embedding.tobytes(), path


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        # Different embedding-model dimensions — e.g. a legacy enrollment
        # made before the real-model swap (512-dim) vs. a live-session
        # embedding from the current SpeechBrain model (192-dim). Not
        # comparable; treat as "definitely not a match" rather than crash.
        return 0.0
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


async def identify_speaker(pcm: bytes) -> tuple[str | None, str | None, float]:
    """Return (speaker_id, role, confidence) or (None, None, score) if unknown."""
    from sqlalchemy import select

    from backend.db.engine import AsyncSessionLocal
    from backend.db.models import VoiceEnrollment

    embedding = await __import__("asyncio").to_thread(embed_provider.extract, pcm)
    best_score, best_match = 0.0, None

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(VoiceEnrollment).where(VoiceEnrollment.status == "ready"))
        rows = result.scalars().all()

    for row in rows:
        if row.embedding:
            # Enrollment always stores float16. 384 bytes = 192-dim (current
            # SpeechBrain ECAPA model); 1024 bytes = 512-dim (legacy
            # hand-rolled model, from before the embedding swap — these rows
            # need re-enrollment to ever match again, see EmbedProvider's
            # docstring). Anything else falls back to a float32 read, for
            # any even-older row that predates float16 storage.
            if len(row.embedding) in (384, 1024):
                stored = np.frombuffer(row.embedding, dtype=np.float16).astype(np.float32)
            else:
                stored = np.frombuffer(row.embedding, dtype=np.float32)
            score = cosine_similarity(embedding, stored)
            if score > best_score:
                best_score = score
                best_match = row

    if best_score >= settings.COSINE_THRESHOLD and best_match:
        return best_match.speaker_name, best_match.role, best_score
    return None, None, best_score
