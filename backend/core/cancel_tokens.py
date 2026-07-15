"""
Concurrency cancel tokens — cancel TTS or background jobs on barge-in.
"""

import asyncio
import logging

logger = logging.getLogger("pilot.cancel")

_tts_task: asyncio.Task | None = None
_bg_tasks: dict[str, asyncio.Task] = {}


def register_tts(task: asyncio.Task):
    global _tts_task
    _tts_task = task


# If the assistant is reading a long response and you speak or say "Stop" or "Wait", the system immediately calls cancel_tts().
def cancel_tts():
    global _tts_task
    if _tts_task and not _tts_task.done():
        _tts_task.cancel()
        logger.info("TTS cancelled (barge-in)")
    _tts_task = None


def register_bg(job_id: str, task: asyncio.Task):
    _bg_tasks[job_id] = task


def cancel_bg(job_id: str):
    t = _bg_tasks.pop(job_id, None)
    if t and not t.done():
        t.cancel()
        logger.info(f"BG job {job_id} cancelled")


def cancel_all_bg():
    for jid in list(_bg_tasks):
        cancel_bg(jid)
