import logging
import os
import re

import numpy as np
import onnxruntime as ort
from transformers import WhisperFeatureExtractor

from backend.queues.bus import QueueBus, TurnSegment
from backend.services.stt import whisper_provider

logger = logging.getLogger("pilot.smart_turn")

# Trailing fragments indicating incomplete thoughts or natural breath pauses
INCOMPLETE_TRAILING = r"\b(and|but|or|if|because|so|then|with|to|the|for|like|um|ah|uh|on|at|by|my|our|your|we|i|they|he|she|who|which|that|what|when|how|is|are|was|were|be|been)$"


class SmartTurnWorker:
    def __init__(self, bus: QueueBus):
        self.bus = bus
        self._buffers: dict[str, list[bytes]] = {}  # session_id -> list of buffered pcm chunks

        # Load SmartTurn ONNX model
        self.model_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../smart-turn-v3.2-cpu.onnx")
        )
        try:
            so = ort.SessionOptions()
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            so.inter_op_num_threads = 1
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self.session = ort.InferenceSession(self.model_path, sess_options=so)
            self.feature_extractor = WhisperFeatureExtractor(chunk_length=8)
            logger.info("Pipecat Smart Turn v3.2 ONNX model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load Smart Turn ONNX model: {e}")
            self.session = None
            self.feature_extractor = None

    def _predict_onnx_smart_turn(self, pcm_data: bytes) -> bool:
        """
        Runs the Pipecat Smart Turn v3.2 ONNX model to predict if a turn is complete.
        Returns True if complete, False if incomplete.
        """
        if self.session is None or self.feature_extractor is None:
            raise RuntimeError("Smart Turn ONNX model is not loaded.")

        audio_array = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0

        # Truncate or pad to exactly the last 8 seconds of speech context (8 * 16000 samples)
        max_samples = 8 * 16000
        if len(audio_array) > max_samples:
            audio_array = audio_array[-max_samples:]
        elif len(audio_array) < max_samples:
            padding = max_samples - len(audio_array)
            audio_array = np.pad(audio_array, (padding, 0), mode="constant", constant_values=0)

        # Process audio using Whisper's feature extractor
        inputs = self.feature_extractor(
            audio_array,
            sampling_rate=16000,
            return_tensors="np",
            padding="max_length",
            max_length=8 * 16000,
            truncation=True,
            do_normalize=True,
        )

        input_features = inputs.input_features.squeeze(0).astype(np.float32)
        input_features = np.expand_dims(input_features, axis=0)  # Add batch dimension

        # Run ONNX inference
        outputs = self.session.run(None, {"input_features": input_features})
        probability = outputs[0][0].item()
        return probability > 0.5

    async def _is_incomplete_by_heuristics(self, pcm_data: bytes, session_id: str) -> tuple[bool, str]:
        """
        Runs the fallback text heuristics to check for filler words or trailing conjunctions.
        """
        # Get raw transcription preview of the current segment
        text = await whisper_provider.transcribe(pcm_data)
        t_clean = text.lower().strip(",.!? ")

        is_filler_only = t_clean in ["um", "ah", "uh", "like", "so"]
        ends_with_conjunction = bool(re.search(INCOMPLETE_TRAILING, t_clean))
        is_extremely_short = len(t_clean.split()) < 3 and not any(
            cmd in t_clean
            for cmd in [
                "next",
                "prev",
                "first",
                "last",
                "slide",
                "stop",
                "confirm",
                "yes",
                "write",
                "email",
                "send",
                "mail",
            ]
        )

        current_buffer = self._buffers[session_id]
        total_buffered_bytes = sum(len(b) for b in current_buffer) + len(pcm_data)
        total_duration_sec = total_buffered_bytes / (16000 * 2)

        is_incomplete = (is_filler_only or ends_with_conjunction or is_extremely_short) and (
            total_duration_sec < 15.0
        )
        return is_incomplete, text

    async def run(self):
        logger.info("SmartTurn worker started")
        while True:
            seg: TurnSegment = await self.bus.turn_q.get()
            try:
                await self._process(seg)
            except Exception as e:
                logger.error(f"SmartTurn error: {e}", exc_info=True)
                # Fallback: always forward to prevent blocking the audio pipeline
                await self.bus.diar_q.put(seg)

    async def _process(self, seg: TurnSegment):
        session_id = seg.session_id
        if session_id not in self._buffers:
            self._buffers[session_id] = []

        is_incomplete = True
        text = ""
        # Only valid as a cache hit for the FINAL combined_pcm below when the
        # heuristics ran on the entire buffered-so-far audio, i.e. there was
        # nothing already buffered — otherwise `text` only covers seg.pcm,
        # not the full turn.
        text_covers_full_turn = False

        # Priority: Run smart-turn ONNX model first
        try:
            current_buffer = self._buffers[session_id]
            full_pcm = b"".join(current_buffer) + seg.pcm

            is_complete = self._predict_onnx_smart_turn(full_pcm)

            if is_complete:
                is_incomplete = False
                logger.info(f"[{session_id[:6]}] SmartTurn priority model: Turn is complete.")
            else:
                logger.info(
                    f"[{session_id[:6]}] SmartTurn priority model: Turn is incomplete. Checking fallback heuristics..."
                )
                is_incomplete, text = await self._is_incomplete_by_heuristics(seg.pcm, session_id)
                text_covers_full_turn = not current_buffer
        except Exception as e:
            logger.error(
                f"[{session_id[:6]}] SmartTurn priority model failed ({e}). Falling back to heuristics..."
            )
            is_incomplete, text = await self._is_incomplete_by_heuristics(seg.pcm, session_id)
            text_covers_full_turn = not self._buffers[session_id]

        if is_incomplete and (text.strip() or self.session is not None):
            logger.info(
                f"[{session_id[:6]}] Semantic VAD: Utterance is incomplete. Buffering PCM to prevent cutoff."
            )
            self._buffers[session_id].append(seg.pcm)
        else:
            # Complete thought or hard duration cap reached
            current_buffer = self._buffers[session_id]
            if current_buffer:
                logger.info(
                    f"[{session_id[:6]}] Semantic VAD: Thought completed. Prepending {len(current_buffer)} buffered segments."
                )
                current_buffer.append(seg.pcm)
                combined_pcm = b"".join(current_buffer)
                self._buffers[session_id] = []

                # Update segment with combined audio
                seg.pcm = combined_pcm
                seg.duration_ms = len(combined_pcm) / 32  # 32 bytes per ms
            elif text_covers_full_turn and text.strip():
                # The heuristics fallback already transcribed exactly this
                # seg's pcm (no prior buffered audio), and it's the whole
                # turn — hand that transcript straight to ASRWorker instead
                # of making it run Whisper over the same bytes again.
                seg.cached_text = text
                logger.info(
                    f"[{session_id[:6]}] Semantic VAD: Reusing heuristics preview transcript, skipping ASR re-transcribe."
                )

            # Forward to diarizer
            await self.bus.diar_q.put(seg)
