'''
protocol schemas and specifications that defines the websocket events channel API between the python backend and the typescript/react frontend.
'''


from dataclasses import dataclass
from typing import Any, Optional

# how python backend structures messages it broadcast over WebSockets.
# web socket event 
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
# tool_blocked      → {tool, speaker, reason}
# route_decision    → {action, tool, speaker}
# session_state     → {state}
# route_page         → {page}
# ping              → {}
