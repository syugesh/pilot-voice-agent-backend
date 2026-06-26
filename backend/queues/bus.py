"""
Queue Bus — all asyncio.Queue singletons.
Pipeline: raw_audio_q → turn_q → diar_q → labeled_turn_q → transcript_q → event_q
"""
import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional
from core.config import settings


@dataclass
class RawAudioChunk:
    pcm: bytes
    session_id: str
    timestamp: float

@dataclass
class TurnSegment:
    pcm: bytes
    session_id: str
    timestamp: float
    duration_ms: float = 0.0

@dataclass
class LabeledTurn:
    pcm: bytes
    session_id: str
    timestamp: float
    speaker_label: str
    speaker_id: Optional[str]
    role: Optional[str]
    confidence: float

@dataclass
class TranscriptSpan:
    text: str
    session_id: str
    speaker_id: Optional[str]
    role: Optional[str]
    confidence: float
    timestamp: float

@dataclass
class PipelineEvent:
    type: str
    payload: Any
    session_id: str


class QueueBus:
    def __init__(self):
        self.raw_audio_q:    asyncio.Queue[RawAudioChunk]  = asyncio.Queue(maxsize=settings.RAW_AUDIO_Q_SIZE)
        self.turn_q:         asyncio.Queue[TurnSegment]    = asyncio.Queue(maxsize=settings.TURN_Q_SIZE)
        self.diar_q:         asyncio.Queue[TurnSegment]    = asyncio.Queue(maxsize=settings.TURN_Q_SIZE)   # NEW: SmartTurn → Diarizer
        self.labeled_turn_q: asyncio.Queue[LabeledTurn]   = asyncio.Queue(maxsize=settings.LABELED_TURN_Q_SIZE)
        self.identity_q:     asyncio.Queue[LabeledTurn]   = asyncio.Queue(maxsize=settings.LABELED_TURN_Q_SIZE)  # NEW: Diarizer → Identity
        self.transcript_q:   asyncio.Queue[TranscriptSpan] = asyncio.Queue(maxsize=settings.TRANSCRIPT_Q_SIZE)
        self.event_q:        asyncio.Queue[PipelineEvent]  = asyncio.Queue(maxsize=settings.EVENT_Q_SIZE)

    async def emit_event(self, event_type: str, payload: Any, session_id: str):
        evt = PipelineEvent(type=event_type, payload=payload, session_id=session_id)
        try:
            self.event_q.put_nowait(evt)
        except asyncio.QueueFull:
            pass


bus = QueueBus()
