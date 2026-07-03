"""
Shared transcript persistence — used for PILOT's spoken side of the conversation.
User speech is persisted separately in pipeline/asr_worker.py; this covers the
preamble/reply/denial messages emitted from front_llm.py and bg_supervisor.py,
which previously only reached the live WebSocket and were never saved, leaving
session history one-sided (user speech only).
"""
import logging, time

logger = logging.getLogger("pilot.transcript_log")


async def persist_pilot_reply(session_id: str, text: str):
    from db.engine import AsyncSessionLocal
    from db.models import TranscriptLog
    try:
        async with AsyncSessionLocal() as db:
            db.add(TranscriptLog(
                session_id=session_id, speaker_id="PILOT",
                role="assistant", text=text,
                confidence=1.0, timestamp=time.time(),
            ))
            await db.commit()
    except Exception as e:
        logger.error(f"Persist PILOT reply failed: {e}", exc_info=True)
