"""Page navigation tool — lets voice commands switch the frontend between
top-level pages (PPT Copilot, Customer Care, Guidelines, About, ...).

Navigation is a frontend-only concern (React Router-style page state), so
this tool doesn't touch the database — it just emits a WS event the
frontend listens for and translates into `store.setPage(...)`.
"""
import asyncio, logging

logger = logging.getLogger("pilot.tools.navigation")

VALID_PAGES = {"dashboard", "ppt", "care", "resolution", "meetings", "guideline", "profile", "settings"}

PAGE_LABELS = {
    "dashboard":  "the Main Dashboard",
    "ppt":        "PPT Copilot",
    "care":       "Travel Planner",
    "resolution": "Customer Resolution",
    "meetings":   "MeetRoom",
    "guideline":  "System Guidelines",
    "profile":    "your Profile",
    "settings":   "Settings",
}


async def navigate_page(args: dict, session_id: str) -> dict:
    page = (args.get("page") or "").strip().lower()
    if page not in VALID_PAGES:
        return {"status": "error", "spoken_reply": "I'm not sure which page you mean."}

    from backend.queues.bus import bus

    async def _delayed_emit():
        # The spoken confirmation ("Opening PPT Copilot!") is the classifier's
        # preamble, synthesized concurrently with this tool running. Switching
        # the page immediately would unmount the current view and tear down
        # its WebSocket before that audio ever reaches the browser, so the
        # user hears nothing. A short delay gives TTS time to land first —
        # once playback has started via the Web Audio API it survives the
        # unmount on its own.
        await asyncio.sleep(1.4)
        await bus.emit_event("navigate_page", {"page": page}, session_id)

    asyncio.create_task(_delayed_emit())
    # preamble already covers the spoken confirmation — same pattern as
    # ppt_navigate/ppt_jump_to_title, which return "" to avoid double TTS.
    return {"status": "ok", "page": page, "spoken_reply": ""}
