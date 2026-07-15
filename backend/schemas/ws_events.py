"""
Shared WS message schemas — FSE-AB shared.
Keep in sync with contracts/ws_events.ts.
"""

from typing import Any, Optional

from pydantic import BaseModel


class WSEvent(BaseModel):
    type: str
    payload: Any


class TranscriptPayload(BaseModel):
    text: str
    speaker: Optional[str]
    role: Optional[str]
    confidence: float
    timestamp: float


class ToolStartPayload(BaseModel):
    job_id: str
    tool: str
    speaker: Optional[str]
    role: Optional[str]


class ToolEndPayload(BaseModel):
    job_id: str
    tool: str
    result: dict
    latency_ms: Optional[float]


class ConfirmPromptPayload(BaseModel):
    tool: str
    speaker: str
    message: str


class PPTCommandPayload(BaseModel):
    action: str
    index: Optional[int] = None
