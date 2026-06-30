"""PPT tools — navigate + jump to slide by number or title + summarize."""
import asyncio, logging
logger = logging.getLogger("pilot.tools.ppt")


async def ppt_navigate(args: dict, session_id: str) -> dict:
    direction = args.get("direction", "next")
    from queues.bus import bus
    from api.ppt import _slide_store, _latest_upload_sid, _current_slide

    slides = _slide_store.get(session_id) or _slide_store.get(_latest_upload_sid, [])
    effective_sid = session_id if session_id in _slide_store else _latest_upload_sid
    total = len(slides)
    current = _current_slide.get(effective_sid, 0)

    if direction == "next":
        if total > 0 and current >= total - 1:
            return {"spoken_reply": f"You've reached the last slide — slide {total} of {total}. That's the end of the presentation."}
        _current_slide[effective_sid] = min(current + 1, max(total - 1, 0))
    elif direction == "prev":
        if current <= 0:
            return {"spoken_reply": "You're already on the first slide."}
        _current_slide[effective_sid] = max(current - 1, 0)
    elif direction == "first":
        _current_slide[effective_sid] = 0
    elif direction == "last":
        _current_slide[effective_sid] = max(total - 1, 0)

    new_idx = _current_slide.get(effective_sid, 0)
    await bus.emit_event("ppt_command", {"action": direction}, session_id)
    return {"status": "ok", "direction": direction, "index": new_idx, "spoken_reply": ""}


async def ppt_jump_to_title(args: dict, session_id: str) -> dict:
    query        = args.get("query", "")
    slide_number = args.get("slide_number")   # already 0-indexed if from keyword fallback
    from queues.bus import bus

    from api.ppt import _slide_store, _latest_upload_sid, _current_slide
    import re

    effective_sid = session_id if session_id in _slide_store else _latest_upload_sid
    slides = _slide_store.get(effective_sid, [])

    # If explicit slide number given, use directly
    if slide_number is not None:
        idx = int(slide_number)
        _current_slide[effective_sid] = idx
        await bus.emit_event("ppt_command", {"action": "goto", "index": idx}, session_id)
        return {"status": "ok", "index": idx, "title": f"Slide {idx+1}", "spoken_reply": ""}

    q = query.lower()

    # Numeric match in query — only allow within actual deck bounds
    m = re.search(r'\b(\d+)\b', q)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(slides):
            _current_slide[effective_sid] = idx
            await bus.emit_event("ppt_command", {"action": "goto", "index": idx}, session_id)
            return {"status": "ok", "index": idx, "spoken_reply": ""}

    # Title fuzzy match
    best_idx, best_score = 0, 0
    for s in slides:
        score = sum(1 for w in q.split() if w in s.get("title","").lower())
        if score > best_score:
            best_score = score; best_idx = s["index"]

    _current_slide[effective_sid] = best_idx
    await bus.emit_event("ppt_command", {"action": "goto", "index": best_idx}, session_id)
    return {"status": "ok", "index": best_idx, "spoken_reply": ""}


async def ppt_delete_slide(args: dict, session_id: str) -> dict:
    from queues.bus import bus
    slide_number = args.get("slide_number")
    if slide_number is not None:
        # Navigate to the target slide first so the confirm modal shows the right one
        await bus.emit_event("ppt_command", {"action": "goto", "index": int(slide_number)}, session_id)
    await bus.emit_event("ppt_command", {"action": "delete"}, session_id)
    label = f"slide {int(slide_number) + 1}" if slide_number is not None else "this slide"
    return {"status": "ok", "spoken_reply": f"Please confirm the deletion of {label} in the popup."}


async def ppt_summarize(args: dict, session_id: str) -> dict:
    from api.ppt import _slide_store, _latest_upload_sid
    slides = _slide_store.get(session_id) or _slide_store.get(_latest_upload_sid, [])
    if not slides:
        return {"spoken_reply": "No presentation is loaded yet. Please upload a PowerPoint file first."}

    lines = []
    for s in slides:
        title = s.get("title", f"Slide {s['index']+1}")
        notes = s.get("notes", "")
        lines.append(f"Slide {s['index']+1}: {title}" + (f" — {notes}" if notes else ""))

    content = "\n".join(lines)
    summary = await _summarize_ollama(content)
    return {"spoken_reply": summary}


async def _summarize_ollama(content: str) -> str:
    def _call() -> str:
        import ollama
        from core.config import settings
        resp = ollama.chat(
            model=settings.OLLAMA_MODEL,
            messages=[
                {"role": "system", "content":
                    "You are a helpful voice assistant. Summarize the presentation in 4-6 natural spoken sentences. "
                    "Mention the main topics and key points. No markdown, no bullet points — plain conversational speech only."},
                {"role": "user", "content": f"Summarize this presentation:\n\n{content[:3000]}"},
            ],
            think=False,
            options={"num_predict": 220},
            stream=False,
        )
        if isinstance(resp, dict):
            return resp["message"]["content"].strip()
        return resp.message.content.strip()

    try:
        return await asyncio.to_thread(_call)
    except Exception as e:
        logger.error(f"ppt_summarize ollama error: {e}")
        return "I wasn't able to summarize the presentation right now. Please try again."
