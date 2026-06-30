"""
WS event schemas — Python side (backend).
Mirror of ws_events.ts. FSE-AB keep both in sync.
"""
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class WSEvent:
    type: str       # see EVENT TYPES below
    payload: Any
    session_id: str

# EVENT TYPES:
# transcript        → TranscriptPayload
# tool_start        → {job_id, tool, speaker, role}
# tool_end          → {job_id, tool, result, latency_ms}
# job_queued        → {job_id, tool, requester, mode}
# confirm_prompt    → {tool, speaker, message}
# ppt_command       → {action, index?}
# tts_audio         → {chunk: number[]}
# tts_stop          → {}
# tool_blocked      → {tool, speaker, reason}
# route_decision    → {action, tool, speaker}
# session_state     → {state}
# ping              → {}
