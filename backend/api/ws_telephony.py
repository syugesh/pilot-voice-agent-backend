"""
Twilio Media Streams ingestion — receives the live audio of a real phone
call (both legs, separately) and feeds it into the same transcription +
sentiment + Customer Resolution pipeline used for browser-mic sessions.

Twilio sends 8kHz mu-law (G.711) audio, base64-encoded, per ~20ms frame,
tagged by `track` ("inbound" = the customer's real phone, "outbound" = the
rep's browser leg) — that tag is a hard signal from the telephony layer
itself, not a diarization guess, so role assignment here is exact.

Simplification: real-time segmentation (VAD/SmartTurn, used for the
browser-mic pipeline) isn't replicated here — audio is instead transcribed
in fixed ~2.5s windows per track. Good enough for live sentiment/resolution
scoring; less precise turn-boundary handling than the mic pipeline.
"""
import asyncio
import audioop  # stdlib in Python <3.13; see requirements.txt note if upgrading
import base64
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("pilot.telephony.stream")
router = APIRouter()

_FLUSH_SECONDS = 2.5
_TRACK_ROLE = {"inbound": "customer", "outbound": "rep"}
_TRACK_LABEL = {"inbound": "Customer", "outbound": "Rep"}


class _TrackBuffer:
    def __init__(self):
        self.pcm16_8k = bytearray()
        self.last_flush = time.monotonic()


@router.websocket("/ws/telephony/{session_id}")
async def ws_telephony(websocket: WebSocket, session_id: str):
    await websocket.accept()
    logger.info(f"Telephony media stream connected: {session_id[:8]}")

    buffers = {"inbound": _TrackBuffer(), "outbound": _TrackBuffer()}
    call_sid = None

    async def _flush(track: str):
        buf = buffers[track]
        if not buf.pcm16_8k:
            return
        raw = bytes(buf.pcm16_8k)
        buf.pcm16_8k.clear()
        buf.last_flush = time.monotonic()

        # 8kHz -> 16kHz, matching what the rest of the ASR pipeline expects
        # (see services/stt.py — trained/tuned around 16kHz PCM16 input).
        pcm16_16k, _ = audioop.ratecv(raw, 2, 1, 8000, 16000, None)

        from backend.services.stt import whisper_provider

        text = await whisper_provider.transcribe(pcm16_16k)
        if not text.strip():
            return

        role = _TRACK_ROLE[track]
        await _emit_span(session_id, text, role, _TRACK_LABEL[track])

    try:
        while True:
            raw_msg = await websocket.receive_text()
            msg = json.loads(raw_msg)
            event = msg.get("event")

            if event == "start":
                call_sid = msg.get("start", {}).get("callSid")
                logger.info(f"[{session_id[:8]}] call started, callSid={call_sid}")

            elif event == "media":
                media = msg.get("media", {})
                track = media.get("track")
                if track not in buffers:
                    continue
                mulaw = base64.b64decode(media.get("payload", ""))
                pcm16 = audioop.ulaw2lin(mulaw, 2)
                buffers[track].pcm16_8k.extend(pcm16)

                if time.monotonic() - buffers[track].last_flush >= _FLUSH_SECONDS:
                    asyncio.create_task(_flush(track))

            elif event == "stop":
                logger.info(f"[{session_id[:8]}] call stream stopped")
                for track in buffers:
                    await _flush(track)
                break

    except WebSocketDisconnect:
        logger.info(f"[{session_id[:8]}] telephony media stream disconnected")
    except Exception as e:
        logger.error(f"[{session_id[:8]}] telephony stream error: {e}", exc_info=True)
    finally:
        for track in buffers:
            await _flush(track)


async def _emit_span(session_id: str, text: str, role: str, speaker_label: str):
    """Mirrors pipeline/asr_worker.py's _emit_span for a browser-mic turn:
    ring buffer, live transcript event, and — customer turns only — the
    same sentiment scoring Customer Resolution's frustration meter reads."""
    from backend.core.session_state import get_state
    from backend.pipeline.asr_worker import _score_sentiment
    from backend.queues.bus import bus

    get_state(session_id).add_span({"speaker": speaker_label, "role": role, "text": text, "confidence": 0.9})

    ts = time.time()
    await bus.emit_event(
        "transcript",
        {"text": text, "speaker": speaker_label, "role": role, "confidence": 0.9, "timestamp": ts},
        session_id,
    )

    if role == "customer":
        asyncio.create_task(_score_sentiment(session_id, text, ts))
