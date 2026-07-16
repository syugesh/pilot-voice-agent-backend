"""
Background job supervisor — runs tools, speaks results via Gemini/Groq/fallback.
"""

import asyncio
import base64
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("pilot.supervisor")


# when PILOT's LLM decides 'this needs a tool call',it packages the request into a "JOB" and hands it to the supervisor  instead of blocking the conversation loop.
@dataclass
class Job:
    job_id: str
    tool: str
    args: dict
    mode: str
    speaker_id: Optional[str]
    role: Optional[str]
    session_id: str


def _fallback_reply(tool: str, result: dict) -> Optional[str]:
    """Hardcoded reply when no API key available."""
    if result.get("status") == "error":
        return (
            result.get("spoken_reply") or f"Sorry, I hit an issue: {result.get('message', 'unknown error')}"
        )
    if tool == "kb_search":
        items = result.get("results", [])
        if not items:
            return "I searched but didn't find anything relevant. Could you rephrase?"
        first = items[0].get("excerpt", "")[:120]
        return f"Found {len(items)} result{'s' if len(items) > 1 else ''}. {first}"
    if tool == "flight_search":
        flights = result.get("flights", [])
        if not flights:
            return "No flights found for those details. Want to try different dates?"
        f = flights[0]
        return (
            f"Found {len(flights)} options. Best is {f.get('airline', '')} "
            f"departing {f.get('dep', '')} for {f.get('price', '')}."
        )
    if tool == "flight_book":
        ref = result.get("booking_ref", "")
        return f"Not Booked! Your reference is {ref}."

    if tool == "crm_lookup":
        c = result.get("customer", {})
        return (
            f"Found {c.get('name', '')} — {c.get('tier', '')} tier, {c.get('total_flights', 0)} flights."
            if c
            else "Customer not found."
        )
    if tool in ("ppt_navigate", "ppt_jump_to_title"):
        return f"Going to slide {result.get('index', 0) + 1}."
    return None


class BGSupervisor:
    # Hard ceiling on any single tool call — this queue has one consumer, so
    # a hung tool otherwise wedges every other queued voice command forever.
    JOB_TIMEOUT_S = 90.0

    def __init__(self):
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._current: Optional[asyncio.Task] = None
        self._current_session_id: Optional[str] = None

    async def submit(self, job: Job):
        if job.mode == "interrupt" and self._current and not self._current.done():
            self._current.cancel()
        await self._queue.put(job)

    def cancel_for_session(self, session_id: str) -> bool:
        """Cancels the currently-executing job if — and only if — it belongs
        to this session, so one session saying "stop" can never cancel a
        different session's in-flight job (the queue/executor are global,
        shared across every concurrent session on this server)."""
        if self._current and not self._current.done() and self._current_session_id == session_id:
            self._current.cancel()
            return True
        return False

    async def run(self):
        logger.info("BGSupervisor started")
        while True:
            job = await self._queue.get()
            self._current_session_id = job.session_id
            self._current = asyncio.create_task(self._execute(job))
            try:
                await self._current
            except asyncio.CancelledError:
                pass

    async def _execute(self, job: Job):
        from backend.core.session_manager import SessionState, session_manager
        from backend.db.engine import AsyncSessionLocal
        from backend.db.models import AuditLog
        from backend.queues.bus import bus
        from backend.tools.registry import TOOL_REGISTRY

        await bus.emit_event(
            "tool_start",
            {
                "job_id": job.job_id,
                "tool": job.tool,
                "speaker": job.speaker_id,
                "role": job.role,
            },
            job.session_id,
        )
        logger.info(f"⏳ [SUPERVISOR] Starting background job: {job.job_id} | Tool: {job.tool}")

        result = {"status": "error", "message": "unknown tool"}
        t0 = time.time()
        try:
            handler = TOOL_REGISTRY.get(job.tool)
            result = (
                await asyncio.wait_for(handler(job.args, job.session_id), timeout=self.JOB_TIMEOUT_S)
                if handler
                else {"status": "error", "message": f"Unknown: {job.tool}"}
            )
        except asyncio.CancelledError:
            result = {"status": "cancelled"}
            raise
        except asyncio.TimeoutError:
            # Defense-in-depth: this queue has exactly one consumer (see
            # run() below), so a tool handler that hangs forever (an
            # unbounded network/LLM call somewhere) would otherwise wedge
            # every other queued voice command behind it indefinitely, with
            # the UI stuck showing "Processing" forever and no error ever
            # surfacing. This ceiling guarantees the queue always keeps
            # moving and the user always gets *some* answer.
            result = {
                "status": "error",
                "message": f"{job.tool} timed out after {self.JOB_TIMEOUT_S:.0f}s",
                "spoken_reply": "Sorry, that took too long and I had to stop waiting. Please try again.",
            }
            logger.error(f"[SUPERVISOR] Tool '{job.tool}' timed out after {self.JOB_TIMEOUT_S:.0f}s — job_id={job.job_id}")
        except Exception as e:
            result = {"status": "error", "message": str(e)}
            logger.error(f"Tool {job.tool}: {e}")
        finally:
            latency = round((time.time() - t0) * 1000, 1)
            await bus.emit_event(
                "tool_end",
                {
                    "job_id": job.job_id,
                    "tool": job.tool,
                    "result": result,
                    "latency_ms": latency,
                },
                job.session_id,
            )
            logger.info(
                f"✅ [SUPERVISOR] Finished background job: {job.job_id} | Tool: {job.tool} | Latency: {latency}ms | Status: {result.get('status')}"
            )

            # Audit log
            try:
                async with AsyncSessionLocal() as db:
                    db.add(
                        AuditLog(
                            session_id=job.session_id,
                            speaker_id=job.speaker_id,
                            role=job.role,
                            action="tool_call",
                            tool=job.tool,
                            decision=result.get("status", "unknown"),
                            latency_ms=latency,
                        )
                    )
                    await db.commit()
            except Exception as e:
                logger.error(f"Audit log failed: {e}")

            # Generate spoken reply, prioritizing direct tool-generated spoken_reply first to prevent double-summarization loops
            reply = result.get("spoken_reply")

            if not reply:
                from backend.services.bg_agent import generate_reply

                reply = await generate_reply(job.tool, result)
                if reply is None:
                    reply = _fallback_reply(job.tool, result)

            if reply:
                # Reset barge_in flag immediately before compiling the new background answer.
                # Any previous interruption that happened during the old preamble phase is now stale and should be cleared.
                from backend.core.session_state import get_state

                state = get_state(job.session_id)
                state.barge_in = False
                state.tts_playing = False

                # If a file was generated, append the actual code content to the text transcript view
                final_text = reply
                if job.tool in ("flight_search", "hotel_search", "train_search") and result.get("status") == "ok" and result.get("text_reply"):
                    # Use the distinct text layout to build UI cards, but keep 'reply' (which TTS speaks) concise!
                    final_text = result.get("text_reply")
                elif job.tool == "write_file" and result.get("status") == "ok" and result.get("content"):
                    fname = result.get("filename", "script.py")
                    # Dynamically map the extension to the correct language formatting syntax
                    ext = os.path.splitext(fname)[1].lower().strip(".") if fname else "py"
                    lang_map = {
                        "py": ("Python", "python"),
                        "cpp": ("C++", "cpp"),
                        "cc": ("C++", "cpp"),
                        "c": ("C", "c"),
                        "js": ("JavaScript", "javascript"),
                        "ts": ("TypeScript", "typescript"),
                        "html": ("HTML", "html"),
                        "css": ("CSS", "css"),
                        "sh": ("Bash", "bash"),
                        "java": ("Java", "java"),
                    }
                    lang_title, lang_syntax = lang_map.get(ext, ("Python", "python"))
                    final_text = f"I have successfully generated your {lang_title} script:\n\n```{lang_syntax}\n# {fname}\n{result.get('content')}\n```"
                elif job.tool == "write_email" and result.get("status") == "ok" and result.get("email_draft"):
                    draft = result.get("email_draft")
                    final_text = f"I have successfully drafted your email:\n\n```email\n{draft}\n```"

                await session_manager.transition(job.session_id, SessionState.SPEAKING)
                await bus.emit_event(
                    "transcript",
                    {
                        "text": final_text,
                        "speaker": "PILOT",
                        "role": "assistant",
                        "confidence": 1.0,
                        "timestamp": time.time(),
                        "job_id": job.job_id,
                    },
                    job.session_id,
                )

                # Cancel any existing active TTS task cleanly (like an active preamble task still sleeping)
                if state.active_tts_task and not state.active_tts_task.done():
                    state.active_tts_task.cancel()
                    logger.info(
                        f"[{job.session_id[:6]}] Cancelled active preamble TTS task to speak main response."
                    )

                task = asyncio.create_task(_speak(reply, job.session_id))
                state.active_tts_task = task
            else:
                await session_manager.transition(job.session_id, SessionState.LISTENING)
                # If there's no spoken reply, emit a fallback transcript event with job_id to complete the task card
                await bus.emit_event(
                    "transcript",
                    {
                        "text": f"✓ {job.tool} task executed.",
                        "speaker": "PILOT",
                        "role": "assistant",
                        "confidence": 1.0,
                        "timestamp": time.time(),
                        "job_id": job.job_id,
                    },
                    job.session_id,
                )


async def _speak(text: str, session_id: str):
    from backend.core.session_manager import SessionState, session_manager
    from backend.core.session_state import get_state
    from backend.queues.bus import bus
    from backend.services.tts import tts_to_bytes

    current_task = asyncio.current_task()
    try:
        state = get_state(session_id)
        # Keep tts_playing False during compilation so late mic frames do not trigger accidental self-interruption
        state.tts_playing = False
        state.barge_in = False

        data, mime = await tts_to_bytes(text)

        # Check if the user interrupted while TTS was compiling
        if state.barge_in:
            logger.info(
                f"[{session_id[:6]}] Interruption detected during TTS compilation. Aborting playback."
            )
            return

        # If a newer TTS task was started, abort gracefully
        if state.active_tts_task is not current_task:
            logger.info(f"[{session_id[:6]}] New TTS task started during compilation. Aborting old playback.")
            return

        if data:
            b64 = base64.b64encode(data).decode("ascii")
            # Set tts_playing to True right before emitting the audio to user's browser
            state.tts_playing = True
            state.tts_start_time = time.time()
            state.barge_in = False

            await bus.emit_event("tts_audio", {"b64": b64, "mime": mime}, session_id)

            # Give the audio a moment to play out before clearing tts_playing
            playback_duration = max(0.5, len(data) / 48000.0)
            await asyncio.sleep(playback_duration)

    except Exception as e:
        logger.error(f"Speak error: {e}")
    finally:
        state = get_state(session_id)
        if state.active_tts_task is current_task:
            state.tts_playing = False
            state.active_tts_task = None
            await session_manager.transition(session_id, SessionState.LISTENING)


bg_supervisor = BGSupervisor()
