"""
Background job supervisor — runs tools, speaks results via Gemini/Groq/fallback.
"""
import asyncio, logging, time, base64
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("pilot.supervisor")


@dataclass
class Job:
    job_id:     str
    tool:       str
    args:       dict
    mode:       str
    speaker_id: Optional[str]
    role:       Optional[str]
    session_id: str


def _fallback_reply(tool: str, result: dict) -> Optional[str]:
    """Hardcoded reply when no API key available."""
    if result.get("status") == "error":
        return result.get("spoken_reply") or f"Sorry, I hit an issue: {result.get('message','unknown error')}"
    if tool == "kb_search":
        items = result.get("results", [])
        if not items:
            return "I searched but didn't find anything relevant. Could you rephrase?"
        first = items[0].get("excerpt","")[:120]
        return f"Found {len(items)} result{'s' if len(items)>1 else ''}. {first}"
    if tool == "flight_search":
        flights = result.get("flights", [])
        if not flights:
            return "No flights found for those details. Want to try different dates?"
        f = flights[0]
        return (f"Found {len(flights)} options. Best is {f.get('airline','')} "
                f"departing {f.get('dep','')} for {f.get('price','')}.")
    if tool == "flight_book":
        ref = result.get("booking_ref","")
        return f"Booked! Your reference is {ref}."
    if tool in ("ticket_create","ticket_update"):
        ref = result.get("ticket_ref","")
        return f"Ticket {ref} created successfully."
    if tool == "ticket_close":
        return "Ticket closed."
    if tool == "crm_lookup":
        c = result.get("customer", {})
        return (f"Found {c.get('name','')} — {c.get('tier','')} tier, "
                f"{c.get('total_flights',0)} flights." if c else "Customer not found.")
    if tool in ("ppt_navigate","ppt_jump_to_title"):
        return f"Going to slide {result.get('index', 0) + 1}."
    return None


class BGSupervisor:
    def __init__(self):
        self._queue:   asyncio.Queue[Job]     = asyncio.Queue()
        self._current: Optional[asyncio.Task] = None

    async def submit(self, job: Job):
        if job.mode == "interrupt" and self._current and not self._current.done():
            self._current.cancel()
        await self._queue.put(job)

    async def run(self):
        logger.info("BGSupervisor started")
        while True:
            job = await self._queue.get()
            self._current = asyncio.create_task(self._execute(job))
            try:
                await self._current
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"BGSupervisor: unhandled task error for {job.tool}: {e}", exc_info=True)

    async def _execute(self, job: Job):
        from tools.registry import TOOL_REGISTRY
        from queues.bus import bus
        from core.session_manager import session_manager, SessionState
        from db.engine import AsyncSessionLocal
        from db.models import AuditLog

        await bus.emit_event("tool_start", {
            "job_id": job.job_id, "tool": job.tool,
            "speaker": job.speaker_id, "role": job.role,
        }, job.session_id)

        result = {"status": "error", "message": "unknown tool"}
        t0 = time.time()
        try:
            handler = TOOL_REGISTRY.get(job.tool)
            result = await handler(job.args, job.session_id) if handler else \
                     {"status": "error", "message": f"Unknown: {job.tool}"}
        except asyncio.CancelledError:
            result = {"status": "cancelled"}; raise
        except Exception as e:
            result = {"status": "error", "message": str(e)}
            logger.error(f"Tool {job.tool}: {e}")
        finally:
            latency = round((time.time() - t0) * 1000, 1)
            await bus.emit_event("tool_end", {
                "job_id": job.job_id, "tool": job.tool,
                "result": result, "latency_ms": latency,
            }, job.session_id)

            # Audit log
            try:
                async with AsyncSessionLocal() as db:
                    db.add(AuditLog(
                        session_id=job.session_id, speaker_id=job.speaker_id,
                        role=job.role, action="tool_call", tool=job.tool,
                        decision=result.get("status","unknown"), latency_ms=latency,
                    ))
                    await db.commit()
            except Exception as e:
                logger.error(f"Audit log failed: {e}")

            # A cancelled job (barge-in / superseded by a newer interrupt-mode job)
            # must stay silent — narrating "the tool was cancelled" back to the user
            # is meaningless noise, not an answer. Skip reply synthesis entirely.
            reply = None
            if result.get("status") != "cancelled":
                # spoken_reply semantics:
                #   absent / None  → auto-generate via bg_agent + fallback
                #   ""             → tool explicitly suppresses TTS (preamble already covered it)
                #   "some text"    → speak exactly this
                reply = result.get("spoken_reply", None)

                if reply is None:
                    from services.bg_agent import generate_reply
                    reply = await generate_reply(job.tool, result)
                    if reply is None:
                        reply = _fallback_reply(job.tool, result)

            if reply:
                # Reset barge_in flag immediately before compiling the new background answer.
                # Any previous interruption that happened during the old preamble phase is now stale and should be cleared.
                from core.session_state import get_state
                state = get_state(job.session_id)
                state.barge_in = False
                state.tts_playing = False

                # If a file was generated, append the actual code content to the text transcript view
                final_text = reply
                if job.tool == "write_file" and result.get("status") == "ok" and result.get("content"):
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
                await bus.emit_event("transcript", {
                    "text": final_text, "speaker": "PILOT", "role": "assistant",
                    "confidence": 1.0, "timestamp": time.time(),
                    "job_id": job.job_id,
                }, job.session_id)
                from core.transcript_log import persist_pilot_reply
                asyncio.create_task(persist_pilot_reply(job.session_id, final_text))

                # Cancel any existing active TTS task cleanly (like an active preamble task still sleeping)
                if state.active_tts_task and not state.active_tts_task.done():
                    state.active_tts_task.cancel()
                    logger.info(f"[{job.session_id[:6]}] Cancelled active preamble TTS task to speak main response.")
                    
                task = asyncio.create_task(_speak(reply, job.session_id))
                state.active_tts_task = task
            elif result.get("status") != "cancelled":
                await session_manager.transition(job.session_id, SessionState.LISTENING)
                # If there's no spoken reply, emit a fallback transcript event with job_id to complete the task card
                await bus.emit_event("transcript", {
                    "text": f"✓ {job.tool} task executed.", "speaker": "PILOT", "role": "assistant",
                    "confidence": 1.0, "timestamp": time.time(),
                    "job_id": job.job_id,
                }, job.session_id)
                from core.transcript_log import persist_pilot_reply
                asyncio.create_task(persist_pilot_reply(job.session_id, f"✓ {job.tool} task executed."))
            else:
                await session_manager.transition(job.session_id, SessionState.LISTENING)


async def _speak(text: str, session_id: str):
    from services.tts import tts_to_bytes
    from queues.bus import bus
    from core.session_manager import session_manager, SessionState
    from core.session_state import get_state

    current_task = asyncio.current_task()
    try:
        state = get_state(session_id)
        # Keep tts_playing False during compilation so late mic frames do not trigger accidental self-interruption
        state.tts_playing = False
        state.barge_in = False
        
        data, mime = await tts_to_bytes(text)
        
        # Check if the user interrupted while TTS was compiling
        if state.barge_in:
            logger.info(f"[{session_id[:6]}] Interruption detected during TTS compilation. Aborting playback.")
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
