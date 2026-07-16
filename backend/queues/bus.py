"""
Queue Bus — all asyncio.Queue singletons.
Pipeline: raw_audio_q → turn_q → diar_q → labeled_turn_q → transcript_q → event_q

Diarization and identity resolution are one combined step (DiarizerWorker
in pipeline/diarizer.py) — a turn goes straight from diar_q to
labeled_turn_q already carrying its resolved speaker_id/role, no separate
identity queue/stage in between.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from backend.core.config import settings

#  a set of asyncio.Queues that decouple each pipeline stage (ASR, diarization, identity, transcription) so they run as independent async producers/consumers instead of being directly wired together.


# raw PCM straight off the mic, tagged with session_id + timestamp.
@dataclass
class RawAudioChunk:
    pcm: bytes
    session_id: str
    timestamp: float


# same PCM, but now segmented into a "turn" (i.e. VAD/SmartTurn decided this chunk is a complete utterance), plus duration_ms
@dataclass
class TurnSegment:
    pcm: bytes
    session_id: str
    timestamp: float
    duration_ms: float = 0.0
    # Set by SmartTurnWorker when its fallback-heuristic pass already
    # transcribed exactly this pcm (no further chunks got appended after
    # that check) — lets ASRWorker skip re-transcribing the same audio.
    cached_text: Optional[str] = None


# a turn that's been through diarization: now has speaker_label, speaker_id, role, confidence.
@dataclass
class LabeledTurn:
    pcm: bytes
    session_id: str
    timestamp: float
    speaker_label: str
    speaker_id: Optional[str]
    role: Optional[str]
    confidence: float
    cached_text: Optional[str] = None


# TranscriptSpan — the ASR  text version of a labeled turn, dropping the PCM since you don't need raw audio anymore.
@dataclass
class TranscriptSpan:
    text: str
    session_id: str
    speaker_id: Optional[str]
    role: Optional[str]
    confidence: float
    timestamp: float


# a generic envelope (type + payload + session_id) used for anything pushed to the frontend/event layer (e.g. "transcript", "speaker_change", etc.)
@dataclass
class PipelineEvent:
    type: str
    payload: Any
    session_id: str


# raw_audio_q → (VAD/SmartTurn) → turn_q → (?) → diar_q → (Diarizer + Identity) → labeled_turn_q → (ASR) → transcript_q → (FrontLLM/persistence) → event_q
class QueueBus:
    def __init__(self):
        self.raw_audio_q: asyncio.Queue[RawAudioChunk] = asyncio.Queue(maxsize=settings.RAW_AUDIO_Q_SIZE)
        self.turn_q: asyncio.Queue[TurnSegment] = asyncio.Queue(maxsize=settings.TURN_Q_SIZE)
        self.diar_q: asyncio.Queue[TurnSegment] = asyncio.Queue(
            maxsize=settings.TURN_Q_SIZE
        )  # NEW: SmartTurn → Diarizer
        self.labeled_turn_q: asyncio.Queue[LabeledTurn] = asyncio.Queue(maxsize=settings.LABELED_TURN_Q_SIZE)
        self.transcript_q: asyncio.Queue[TranscriptSpan] = asyncio.Queue(maxsize=settings.TRANSCRIPT_Q_SIZE)
        self.event_q: asyncio.Queue[PipelineEvent] = asyncio.Queue(maxsize=settings.EVENT_Q_SIZE)

    # This is the sink for anything that needs to reach the frontend (via event_q) or get durably persisted (DB)
    async def emit_event(self, event_type: str, payload: Any, session_id: str):
        evt = PipelineEvent(type=event_type, payload=payload, session_id=session_id)
        try:
            self.event_q.put_nowait(evt)
        except asyncio.QueueFull:
            pass

            # puspose is to persist transcribed speech segments and metadata async into the database for auditing , hostory tracking and session summaries. 
            
        if event_type == "transcript":
            asyncio.create_task(self._persist_transcript_async(session_id, payload))

    async def _persist_transcript_async(self, session_id: str, payload: Any):
        import logging

        from backend.db.engine import AsyncSessionLocal
        from backend.db.models import TranscriptLog

        try:
            async with AsyncSessionLocal() as db:
                db.add(
                    TranscriptLog(
                        session_id=session_id,
                        speaker_id=payload.get("speaker") or payload.get("speaker_id"),
                        role=payload.get("role"),
                        text=payload.get("text", ""),
                        confidence=payload.get("confidence", 1.0),
                        timestamp=payload.get("timestamp") or 0.0,
                    )
                )
                await db.commit()
        except Exception as e:
            logging.getLogger("pilot.bus").error(f"Async transcript persist failed: {e}")


bus = QueueBus()
