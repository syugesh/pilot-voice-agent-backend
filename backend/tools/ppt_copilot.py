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


async def ppt_last_action(args: dict, session_id: str) -> dict:
    from core.session_state import get_state
    state = get_state(session_id)
    last = state.last_ppt_action or {}
    if not last:
        return {"spoken_reply": "I haven't changed the deck yet in this session."}
    slide_no = last.get("slide_number")
    instruction = last.get("instruction", "your last request")
    changes = last.get("changes") or []
    if changes:
        changed_text = ", ".join(changes)
        return {"spoken_reply": f"Yes. I updated slide {slide_no}: {changed_text}, based on your request to {instruction}."}
    return {"spoken_reply": f"I tried to update slide {slide_no} based on your request to {instruction}, but I don't see a content change."}


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


async def _apply_edit_and_background_refresh(effective_sid: str, idx: int, session_id: str, writer) -> bool:
    """
    Writes an edit immediately (fast — well under a second) so the caller
    can speak a confirmation right away, then regenerates thumbnails
    (LibreOffice re-rendering the whole deck — several seconds) as a
    background task, emitting the same "refresh" ppt_command once that
    finishes instead of making the voice reply wait for it too. The
    thumbnail visually catches up a couple seconds after the spoken
    confirmation — the frontend already refetches on that event regardless
    of when it fires.
    Returns True on success, False if the session/slide index was invalid.
    """
    from api.ppt import apply_slide_edit_fast_async, refresh_slide_thumbnails_async
    from queues.bus import bus

    ok = await apply_slide_edit_fast_async(effective_sid, idx, writer)
    if not ok:
        return False

    async def _background_refresh():
        await refresh_slide_thumbnails_async(effective_sid)
        await bus.emit_event("ppt_command", {"action": "refresh", "session_id": effective_sid}, session_id)

    asyncio.create_task(_background_refresh())
    return True


async def ppt_edit_slide(args: dict, session_id: str) -> dict:
    instruction = args.get("instruction", "")
    if not instruction:
        return {"spoken_reply": "What would you like me to change?"}

    from api.ppt import _slide_store, _latest_upload_sid, _current_slide
    from queues.bus import bus
    import re

    effective_sid = session_id if session_id in _slide_store else _latest_upload_sid
    slides = _slide_store.get(effective_sid, [])
    if not slides:
        return {"spoken_reply": "No presentation is loaded."}
        
    slide_number = args.get("slide_number")
    idx = int(slide_number) if slide_number is not None else _current_slide.get(effective_sid, 0)
    if idx < 0 or idx >= len(slides):
        return {"spoken_reply": "I'm not sure which slide to edit."}
    _current_slide[effective_sid] = idx
    await bus.emit_event("ppt_command", {"action": "goto", "index": idx}, session_id)

    slide = slides[idx]

    # Slides generated from the GD template have a known "kind" (team,
    # table, comparison, ...) whose content doesn't fit the generic
    # title/numbered-bullets model at all — route those through the
    # kind-aware editor instead. Uploaded/legacy decks have no kind and
    # fall straight through to the unchanged generic path below.
    kind = slide.get("kind")
    if kind:
        return await _edit_kind_aware_slide(effective_sid, idx, kind, instruction, session_id)

    # Extract bullets
    old_bullets = []
    for sh in slide.get("shapes", []):
        for line in sh.get("text", "").split("\n"):
            m = re.match(r'^\d+\.\s{1,3}(.+)', line)
            if m:
                old_bullets.append(m.group(1).strip())
                
    old_title = slide.get("title", "")
    old_notes = slide.get("notes", "")

    # Every other text-bearing shape that ISN'T the title or a numbered bullet
    # line (e.g. a subtitle, caption, or footer). Keyed by stable shape_id so
    # the editor can target one exact shape without touching anything else on
    # the slide — this is what lets the user edit "anything on the slide",
    # not just the title/bullets/notes triplet.
    other_shapes: dict[str, str] = {}
    for sh in slide.get("shapes", []):
        sid = sh.get("shape_id")
        text = sh.get("text", "").strip()
        if sid is None or not text:
            continue
        if text == old_title:
            continue
        if all(re.match(r'^\d+\.\s{1,3}', line) for line in text.split("\n") if line.strip()):
            continue  # this is the bullet body shape, already covered above
        other_shapes[str(sid)] = text

    # Common voice correction: "change the title of slide 3 from X to Y".
    # Handle it deterministically so ASR/correction commands don't depend on
    # the editor LLM preserving the exact replacement text.
    title_to = re.search(r'\btitle\b.*?\bfrom\b.+?\bto\b\s+(.+)$', instruction, re.IGNORECASE)
    title_direct = re.search(r'\b(?:change|update|set|rename)\b.*?\btitle\b.*?\bto\b\s+(.+)$', instruction, re.IGNORECASE)
    replacement_title = (title_to or title_direct)
    if replacement_title:
        new_title = replacement_title.group(1).strip().strip('".,!?')
        if new_title:
            try:
                from api.ppt import _patch_slide
                # other_edits intentionally omitted (defaults to None): a
                # title-only instruction must never touch bullets, notes, or
                # any other shape on the slide.
                def _writer(slide, _t=new_title, _b=old_bullets, _n=old_notes):
                    _patch_slide(slide, idx, _t, _b, _n, None)
                await _apply_edit_and_background_refresh(effective_sid, idx, session_id, _writer)
                from core.session_state import get_state
                state = get_state(session_id)
                state.last_ppt_action = {
                    "tool": "ppt_edit_slide",
                    "slide_number": idx + 1,
                    "instruction": instruction,
                    "changes": [f"title from '{old_title}' to '{new_title}'"],
                }
                return {"spoken_reply": f"I changed the title on slide {idx + 1} to {new_title}."}
            except Exception as e:
                logger.error(f"ppt_edit_slide title shortcut error: {e}")
                return {"spoken_reply": "I had trouble editing the slide right now."}

    # LLM call
    def _call() -> str:
        import ollama
        from core.config import settings
        import json
        other_shapes_desc = (
            "\n".join(f'- shape_id {sid}: "{text}"' for sid, text in other_shapes.items())
            if other_shapes else "(none)"
        )
        prompt = f"""
You are an expert presentation editor. The user wants to edit a slide.
Current Slide:
Title: {old_title}
Bullets: {json.dumps(old_bullets)}
Notes: {old_notes}
Other text on this slide (subtitle/caption/footer — NOT the title or bullets):
{other_shapes_desc}

Instruction: {instruction}

Rules:
- Only change what the instruction actually asks for. Fields you are not asked
  to change must be echoed back EXACTLY as given above — do not paraphrase,
  shorten, or drop unrelated text.
- If the instruction targets one of the "other text" shapes above (a subtitle,
  caption, footer, etc.), put its new text in "other_edits" keyed by its
  shape_id. Do not invent shape_ids that weren't listed. Leave "other_edits"
  as {{}} if nothing else needs to change.

Output ONLY valid JSON matching this schema:
{{
  "title": "New Title",
  "bullets": ["New Bullet 1", "New Bullet 2"],
  "notes": "New Speaker Notes",
  "other_edits": {{"<shape_id>": "new text for that shape"}}
}}
"""
        resp = ollama.chat(
            model=settings.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            # think=False is required alongside format="json" — without it, a
            # reasoning-capable model (Qwen3, etc.) tries to emit chain-of-thought
            # before its JSON, which conflicts with grammar-constrained JSON
            # decoding and can come back as empty content (see _summarize_ollama
            # above, and _generate_template_content_sync in
            # services/ppt_template_builder.py, for the same fix).
            options={"num_predict": 400},
            format="json",
            think=False,
            stream=False,
        )
        if isinstance(resp, dict):
            return resp["message"]["content"].strip()
        return resp.message.content.strip()

    try:
        raw = await asyncio.to_thread(_call)
        import json
        if not raw:
            # Same empty-content failure mode this fix targets — retry once
            # before giving up, since Ollama occasionally still returns a
            # blank message.strip() on a cold model load.
            raw = await asyncio.to_thread(_call)
        if not raw:
            logger.error("ppt_edit_slide error: Ollama returned an empty response after retry")
            return {"spoken_reply": "I had trouble understanding that edit — could you try rephrasing it?"}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.error(f"ppt_edit_slide error: invalid JSON from Ollama: {raw[:300]!r}")
            return {"spoken_reply": "I had trouble understanding that edit — could you try rephrasing it?"}
        new_title = data.get("title", old_title)
        new_bullets = data.get("bullets", old_bullets)
        new_notes = data.get("notes", old_notes)

        # Only accept other_edits for shape_ids we actually listed to the
        # model — guards against a hallucinated id landing on the wrong shape.
        raw_other_edits = data.get("other_edits") or {}
        other_edits = {
            sid: text for sid, text in raw_other_edits.items()
            if sid in other_shapes and isinstance(text, str) and text.strip()
        }

        changes = []
        if new_title != old_title:
            changes.append(f"title from '{old_title}' to '{new_title}'")
        if new_bullets != old_bullets:
            changes.append("bullet content")
        if new_notes != old_notes:
            changes.append("speaker notes")
        for sid, text in other_edits.items():
            if text != other_shapes.get(sid):
                changes.append("other slide text")
                break
        
        from api.ppt import _patch_slide
        def _writer(slide, _t=new_title, _b=new_bullets, _n=new_notes, _oe=other_edits or None):
            _patch_slide(slide, idx, _t, _b, _n, _oe)
        await _apply_edit_and_background_refresh(effective_sid, idx, session_id, _writer)
        from core.session_state import get_state
        state = get_state(session_id)
        state.last_ppt_action = {
            "tool": "ppt_edit_slide",
            "slide_number": idx + 1,
            "instruction": instruction,
            "changes": changes,
        }
        return {"spoken_reply": f"I've updated slide {idx + 1}."}
    except Exception as e:
        logger.error(f"ppt_edit_slide error: {e}")
        return {"spoken_reply": "I had trouble editing the slide right now."}


async def _edit_kind_aware_slide(effective_sid: str, idx: int, kind: str, instruction: str, session_id: str) -> dict:
    """
    Voice-edit path for slides cloned from the GD template (team, table,
    comparison, ...) — the generic title/numbered-bullets model above
    doesn't fit their structure at all. Same "send current state as JSON,
    ask the model for edited JSON back" pattern as the generic path, just
    with each kind's own field schema instead of title/bullets/notes.
    """
    from services.ppt_template_builder import extract_slide_data, populate_slide_data
    import json

    def _read_current() -> dict:
        from pptx import Presentation
        prs = Presentation(f"data/ppt/{effective_sid}.pptx")
        return extract_slide_data(prs.slides[idx], kind)

    try:
        current_data = await asyncio.to_thread(_read_current)
    except Exception as e:
        logger.error(f"kind-aware edit: failed to read current slide data: {e}")
        return {"spoken_reply": "I had trouble reading that slide right now."}

    def _call() -> str:
        import ollama
        from core.config import settings
        prompt = f"""
You are an expert presentation editor. The user wants to edit a "{kind}" slide.

Current content (JSON): {json.dumps(current_data)}

Instruction: {instruction}

Rules:
- Return the SAME JSON shape as "Current content" above, with the requested
  change applied.
- Only change what the instruction actually asks for — every other field
  must be echoed back EXACTLY as given, do not paraphrase or drop anything.
- Output ONLY valid JSON — no markdown, no explanation, nothing else.
"""
        resp = ollama.chat(
            model=settings.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"num_predict": 500},
            format="json",
            think=False,  # see ppt_edit_slide above for why this must pair with format="json"
            stream=False,
        )
        if isinstance(resp, dict):
            return resp["message"]["content"].strip()
        return resp.message.content.strip()

    try:
        raw = await asyncio.to_thread(_call)
        if not raw:
            raw = await asyncio.to_thread(_call)  # cold-model-load retry, same as the generic path
        if not raw:
            logger.error("kind-aware edit error: Ollama returned an empty response after retry")
            return {"spoken_reply": "I had trouble understanding that edit — could you try rephrasing it?"}
        try:
            new_data = json.loads(raw)
        except json.JSONDecodeError:
            logger.error(f"kind-aware edit error: invalid JSON from Ollama: {raw[:300]!r}")
            return {"spoken_reply": "I had trouble understanding that edit — could you try rephrasing it?"}

        def _writer(slide):
            populate_slide_data(slide, kind, new_data)

        await _apply_edit_and_background_refresh(effective_sid, idx, session_id, _writer)
        from core.session_state import get_state
        state = get_state(session_id)
        state.last_ppt_action = {
            "tool": "ppt_edit_slide",
            "slide_number": idx + 1,
            "instruction": instruction,
            "changes": ["slide content"],
        }
        return {"spoken_reply": f"I've updated slide {idx + 1}."}
    except Exception as e:
        logger.error(f"kind-aware ppt_edit_slide error: {e}")
        return {"spoken_reply": "I had trouble editing the slide right now."}


def _slide_bullets_for_notes(slide: dict) -> list:
    import re
    bullets = []
    for sh in slide.get("shapes", []):
        for line in sh.get("text", "").split("\n"):
            m = re.match(r'^\d+\.\s{1,3}(.+)', line)
            if m:
                bullets.append(m.group(1).strip())
    return bullets


def _generate_notes_text_sync(title: str, bullets: list) -> str:
    import ollama, json
    from core.config import settings
    prompt = f"""
Write 3-4 natural conversational sentences of speaker notes for this slide.
Title: {title}
Bullets: {json.dumps(bullets)}

Output the speaker notes plainly without any markdown, prefix, or JSON formatting.
"""
    resp = ollama.chat(
        model=settings.OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"num_predict": 250},
        stream=False,
    )
    if isinstance(resp, dict):
        return resp["message"]["content"].strip()
    return resp.message.content.strip()


async def ppt_generate_notes(args: dict, session_id: str) -> dict:
    from api.ppt import (
        _slide_store, _latest_upload_sid, _current_slide,
        apply_notes_batch_async,
    )
    from queues.bus import bus

    effective_sid = session_id if session_id in _slide_store else _latest_upload_sid
    slides = _slide_store.get(effective_sid, [])
    if not slides:
        return {"spoken_reply": "No presentation is loaded."}

    if args.get("all"):
        await bus.emit_event("ppt_command", {"action": "goto", "index": 0}, session_id)
        notes_by_index: dict[int, str] = {}
        failures = 0
        for i, slide in enumerate(slides):
            try:
                notes_by_index[i] = await asyncio.to_thread(
                    _generate_notes_text_sync, slide.get("title", ""), _slide_bullets_for_notes(slide),
                )
            except Exception as e:
                failures += 1
                logger.error(f"ppt_generate_notes (all) slide {i} error: {e}")
        if not notes_by_index:
            return {"spoken_reply": "I had trouble generating notes right now."}
        await apply_notes_batch_async(effective_sid, notes_by_index)
        await bus.emit_event("ppt_command", {"action": "refresh", "session_id": effective_sid}, session_id)
        from core.session_state import get_state
        state = get_state(session_id)
        state.last_ppt_action = {
            "tool": "ppt_generate_notes",
            "slide_number": None,
            "instruction": "generate speaker notes for all slides",
            "changes": [f"speaker notes for {len(notes_by_index)} of {len(slides)} slides"],
        }
        if failures:
            return {"spoken_reply": f"I've generated speaker notes for {len(notes_by_index)} of {len(slides)} slides — {failures} failed."}
        return {"spoken_reply": f"I've generated speaker notes for all {len(slides)} slides."}

    slide_number = args.get("slide_number")
    idx = int(slide_number) if slide_number is not None else _current_slide.get(effective_sid, 0)
    if idx < 0 or idx >= len(slides):
        return {"spoken_reply": "I'm not sure which slide to write notes for."}
    _current_slide[effective_sid] = idx
    await bus.emit_event("ppt_command", {"action": "goto", "index": idx}, session_id)
    slide = slides[idx]
    old_title = slide.get("title", "")
    old_bullets = _slide_bullets_for_notes(slide)

    try:
        notes = await asyncio.to_thread(_generate_notes_text_sync, old_title, old_bullets)
        # Apply notes, keep title and bullets the same
        from api.ppt import _patch_slide
        def _writer(slide, _t=old_title, _b=old_bullets, _n=notes):
            _patch_slide(slide, idx, _t, _b, _n, None)
        await _apply_edit_and_background_refresh(effective_sid, idx, session_id, _writer)
        from core.session_state import get_state
        state = get_state(session_id)
        state.last_ppt_action = {
            "tool": "ppt_generate_notes",
            "slide_number": idx + 1,
            "instruction": "generate speaker notes",
            "changes": ["speaker notes"],
        }
        return {"spoken_reply": f"I've generated new speaker notes for slide {idx + 1}."}
    except Exception as e:
        logger.error(f"ppt_generate_notes error: {e}")
        return {"spoken_reply": "I had trouble generating notes right now."}


async def ppt_add_slide(args: dict, session_id: str) -> dict:
    """Insert a new slide into the loaded presentation, content generated
    from a spoken description ("add a slide about our Q4 roadmap"). Falls
    back to a plain title-only text slide if generation fails or no
    description was given, rather than failing the whole command."""
    from api.ppt import _slide_store, _latest_upload_sid
    from services.ppt_template_builder import generate_single_slide_content

    effective_sid = session_id if session_id in _slide_store else _latest_upload_sid
    slides = _slide_store.get(effective_sid, [])
    if not slides:
        return {"spoken_reply": "No presentation is loaded yet. Please upload or create one first."}

    instruction = args.get("instruction", "").strip()
    slide_data = None
    if instruction:
        try:
            slide_data = await generate_single_slide_content(instruction)
        except Exception as e:
            logger.error(f"ppt_add_slide generation error: {e}")

    if not slide_data:
        title = instruction[:60] if instruction else "New Slide"
        slide_data = {"kind": "text", "title": title, "paragraphs": []}

    kind = slide_data.pop("kind")

    try:
        ok = await _apply_add_and_background_refresh(effective_sid, kind, slide_data, session_id)
        if not ok:
            return {"spoken_reply": "I couldn't add a slide right now."}
        from core.session_state import get_state
        state = get_state(session_id)
        state.last_ppt_action = {
            "tool": "ppt_add_slide",
            "slide_number": len(slides) + 1,
            "instruction": instruction or "add a new slide",
            "changes": [f"new {kind} slide"],
        }
        return {"spoken_reply": f"I've added a new slide — {slide_data.get('title', 'untitled')}."}
    except Exception as e:
        logger.error(f"ppt_add_slide error: {e}")
        return {"spoken_reply": "I had trouble adding that slide."}


async def _apply_add_and_background_refresh(effective_sid: str, kind: str, data: dict, session_id: str) -> bool:
    """Same fast-reply-then-background-refresh pattern as
    _apply_edit_and_background_refresh, for the add-slide path: writes the
    new slide immediately, then regenerates thumbnails (LibreOffice, whole
    deck) in the background and re-emits ppt_command so the viewer catches
    up a moment after the spoken confirmation instead of before it."""
    from api.ppt import add_slide_fast_async, refresh_slide_thumbnails_async
    from queues.bus import bus

    new_index = await add_slide_fast_async(effective_sid, kind, data)
    if new_index is None:
        return False

    async def _background_refresh():
        await refresh_slide_thumbnails_async(effective_sid)
        await bus.emit_event("ppt_command", {"action": "goto", "index": new_index}, session_id)
        await bus.emit_event("ppt_command", {"action": "refresh", "session_id": effective_sid}, session_id)

    asyncio.create_task(_background_refresh())
    return True