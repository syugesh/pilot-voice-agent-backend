"""PPT API — navigate + file upload + AI generation with slide extraction."""
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os, json, asyncio, subprocess, shutil, glob, logging, time, re

logger = logging.getLogger("pilot.ppt")

router = APIRouter()

class PPTCmd(BaseModel):
    session_id: str
    direction:  str
    slide_index: int = -1

class JumpCmd(BaseModel):
    session_id: str
    query:      str

_slide_store: dict[str, list[dict]] = {}   # session_id → [{title, index, thumb}]
_ppt_titles:  dict[str, str]       = {}   # session_id → presentation title (for download filename)
_latest_upload_sid: str = ""               # fallback key for voice-session lookups
_current_slide: dict[str, int] = {}        # session_id → 0-indexed current slide

_INDEX_PATH = "data/ppt/index.json"


def _load_index() -> list[dict]:
    try:
        if os.path.exists(_INDEX_PATH):
            with open(_INDEX_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_index_entry(session_id: str, title: str, description: str, slide_count: int):
    os.makedirs("data/ppt", exist_ok=True)
    entries = _load_index()
    # Remove any prior entry for this session (re-generation)
    entries = [e for e in entries if e.get("session_id") != session_id]
    entries.insert(0, {
        "session_id":  session_id,
        "title":       title,
        "description": description,
        "slide_count": slide_count,
        "created_at":  __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    })
    with open(_INDEX_PATH, "w") as f:
        json.dump(entries[:50], f, indent=2)   # keep last 50


@router.post("/navigate")
async def navigate(cmd: PPTCmd):
    from tools.ppt_copilot import ppt_navigate
    return await ppt_navigate({"direction": cmd.direction}, cmd.session_id)


@router.post("/jump")
async def jump(cmd: JumpCmd):
    """Fuzzy match slide title → navigate to it."""
    from queues.bus import bus
    slides = _slide_store.get(cmd.session_id, [])
    q = cmd.query.lower()
    best_idx = -1
    best_score = 0
    for s in slides:
        title = s.get("title","").lower()
        score = sum(1 for word in q.split() if word in title)
        # Also match slide number directly: "slide 42" → index 41
        import re
        m = re.search(r'\b(\d+)\b', q)
        if m:
            num = int(m.group(1)) - 1  # 0-indexed
            if 0 <= num < len(slides):
                best_idx = num
                best_score = 99
                break
        if score > best_score:
            best_score = score
            best_idx = s.get("index", -1)

    if best_idx >= 0:
        await bus.emit_event("ppt_command", {"action": "goto", "index": best_idx}, cmd.session_id)
        title = slides[best_idx]["title"] if best_idx < len(slides) else f"Slide {best_idx+1}"
        return {"status": "ok", "index": best_idx, "title": title}
    return {"status": "not_found"}


def _find_soffice() -> str | None:
    """Find LibreOffice executable on macOS or Linux."""
    candidates = [
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        shutil.which("libreoffice"),
        shutil.which("soffice"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def _find_pdftoppm() -> str | None:
    candidates = [
        "/opt/homebrew/bin/pdftoppm",
        shutil.which("pdftoppm"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def _convert_to_images_sync(pptx_path: str, out_dir: str) -> list[str]:
    """Convert every slide to a PNG: PPTX → PDF (LibreOffice) → PNGs (pdftoppm)."""
    soffice = _find_soffice()
    if not soffice:
        return []
    os.makedirs(out_dir, exist_ok=True)

    # Clear stale output from a previous upload into this same slot — otherwise
    # leftover slide-N.png from a bigger deck lingers on disk after a smaller
    # deck is uploaded in its place.
    for stale in glob.glob(os.path.join(out_dir, "slide-*.png")) + glob.glob(os.path.join(out_dir, "*.pdf")):
        try:
            os.remove(stale)
        except OSError:
            pass

    # Step 1: PPTX → PDF — LibreOffice produces a faithful multi-page PDF
    r = subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", out_dir, pptx_path],
        capture_output=True, text=True, timeout=120,
    )
    basename = os.path.splitext(os.path.basename(pptx_path))[0]
    pdf_path = os.path.join(out_dir, f"{basename}.pdf")
    if r.returncode != 0 or not os.path.exists(pdf_path):
        logger.error(f"LibreOffice PDF conversion failed: {r.stderr[:300]}")
        return []

    # Step 2: PDF pages → PNGs via pdftoppm (installed via: brew install poppler)
    pdftoppm = _find_pdftoppm()
    if not pdftoppm:
        logger.warning("pdftoppm not found — install poppler: brew install poppler")
        return []

    prefix = os.path.join(out_dir, "slide")
    r2 = subprocess.run(
        [pdftoppm, "-png", "-r", "150", pdf_path, prefix],
        capture_output=True, text=True, timeout=120,
    )
    if r2.returncode != 0:
        logger.error(f"pdftoppm failed: {r2.stderr[:300]}")
        return []

    # pdftoppm outputs: slide-1.png, slide-2.png … (or slide-01.png with zero-padding)
    images = sorted(glob.glob(os.path.join(out_dir, "slide-*.png")),
                    key=lambda p: int("".join(filter(str.isdigit, os.path.basename(p))) or "0"))
    logger.info(f"Converted {len(images)} slides to PNG")
    return images


@router.post("/upload")
async def upload_ppt(session_id: str, file: UploadFile = File(...)):
    """Accept .pptx, convert slides to images (LibreOffice) + extract text metadata."""
    if not file.filename.endswith((".pptx", ".ppt")):
        raise HTTPException(400, "Only .pptx files supported")
    content = await file.read()
    os.makedirs("data/ppt", exist_ok=True)
    path = f"data/ppt/{session_id}.pptx"
    with open(path, "wb") as f:
        f.write(content)

    # Convert to slide images (faithful visual) — run in thread (blocking)
    img_dir   = f"data/ppt/slides/{session_id}"
    img_paths = await asyncio.to_thread(_convert_to_images_sync, path, img_dir)

    # Extract text metadata for voice navigation + summarise
    slides = await _extract_slides(path)

    # Attach image URL to each slide if conversion succeeded. The version query
    # param forces the browser to refetch even when session_id + filename are
    # identical to a previous upload (e.g. repeated uploads into the same
    # session, or the "default" slot used before a live session exists) —
    # without it the <img>  src string never changes and the browser keeps
    # showing the previous deck's already-decoded image.
    version = int(time.time() * 1000)
    for i, slide in enumerate(slides):
        if i < len(img_paths):
            fname = os.path.basename(img_paths[i])
            slide["image_url"] = f"/api/v1/ppt/image/{session_id}/{fname}?v={version}"

    global _latest_upload_sid
    _slide_store[session_id] = slides
    _latest_upload_sid = session_id
    _current_slide[session_id] = 0
    return {"status": "ok", "slide_count": len(slides), "slides": slides}


@router.get("/image/{session_id}/{filename}")
async def serve_slide_image(session_id: str, filename: str):
    """Serve a converted slide PNG."""
    # Basic path-traversal guard
    if ".." in filename or "/" in filename:
        raise HTTPException(400, "Invalid filename")
    path = f"data/ppt/slides/{session_id}/{filename}"
    if not os.path.exists(path):
        raise HTTPException(404, "Slide image not found")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/slides/{session_id}")
async def get_slides(session_id: str):
    return {"slides": _slide_store.get(session_id, [])}


class GenerateRequest(BaseModel):
    session_id:  str
    description: str
    slide_count: int = 10


@router.post("/generate")
async def generate_ppt(req: GenerateRequest):
    """
    AI-generate a .pptx from a text description.
    Uses Ollama (local, free) to produce slide JSON, then python-pptx to build the file.
    Returns the same slide format as /upload so the viewer loads immediately.
    """
    if not req.description.strip():
        raise HTTPException(400, "Description cannot be empty")
    slide_count = max(3, min(req.slide_count, 20))

    # Step 1 — generate slide content with Ollama
    content = await asyncio.to_thread(_generate_content_sync, req.description, slide_count)
    if not content:
        raise HTTPException(502, "Ollama content generation failed — is Ollama running?")

    # Step 2 — fetch Pexels images for every content slide in parallel
    from core.config import settings
    pexels_images: dict[int, bytes] = {}
    if settings.PEXELS_API_KEY:
        slide_list = content.get("slides", [])
        # Skip cover (idx=0) and last slide — they use full-width layout
        queries = {
            i: slide_list[i].get("image_query") or slide_list[i].get("title", "")
            for i in range(1, len(slide_list) - 1)
        }
        results = await asyncio.gather(
            *[_fetch_pexels_image(q, settings.PEXELS_API_KEY) for q in queries.values()],
            return_exceptions=True,
        )
        for idx, img in zip(queries.keys(), results):
            if isinstance(img, bytes):
                pexels_images[idx] = img
        logger.info(f"Pexels: {len(pexels_images)}/{len(queries)} images fetched")

    # Step 3 — build .pptx with python-pptx
    os.makedirs("data/ppt", exist_ok=True)
    pptx_path = f"data/ppt/{req.session_id}.pptx"
    await asyncio.to_thread(_build_pptx, content, pptx_path, pexels_images)

    # Step 4 — convert to slide images if LibreOffice is available (same as upload)
    img_dir   = f"data/ppt/slides/{req.session_id}"
    img_paths = await asyncio.to_thread(_convert_to_images_sync, pptx_path, img_dir)

    # Step 5 — extract metadata into the same format the viewer expects
    slides = await _extract_slides(pptx_path)
    version = int(time.time() * 1000)
    for i, slide in enumerate(slides):
        if i < len(img_paths):
            fname = os.path.basename(img_paths[i])
            slide["image_url"] = f"/api/v1/ppt/image/{req.session_id}/{fname}?v={version}"

    global _latest_upload_sid
    _slide_store[req.session_id] = slides
    _latest_upload_sid = req.session_id
    _current_slide[req.session_id] = 0

    title = content.get("presentation_title", "Generated Presentation")
    _ppt_titles[req.session_id] = title
    _save_index_entry(req.session_id, title, req.description, len(slides))
    logger.info(f"Generated '{title}' — {len(slides)} slides for session {req.session_id[:8]}")
    return {"status": "ok", "slide_count": len(slides), "slides": slides, "title": title}


@router.get("/download/{session_id}")
async def download_ppt(session_id: str):
    """Download the .pptx file for this session (works for both uploaded and generated files)."""
    # Basic path-traversal guard
    if re.search(r'[/\\.]\.', session_id):
        raise HTTPException(400, "Invalid session id")
    path = f"data/ppt/{session_id}.pptx"
    if not os.path.exists(path):
        raise HTTPException(404, "No presentation found for this session")
    raw_title = _ppt_titles.get(session_id) or "presentation"
    safe_name = re.sub(r'[^\w\s-]', '', raw_title)[:60].strip().replace(' ', '_') or "presentation"
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{safe_name}.pptx",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.pptx"'},
    )


@router.get("/history")
async def get_history():
    """Return list of all previously generated presentations, newest first."""
    return {"history": _load_index()}


@router.get("/history/load/{session_id}")
async def load_history(session_id: str):
    """Reload a previously generated PPT into the viewer."""
    if re.search(r'[/\\.]\.', session_id):
        raise HTTPException(400, "Invalid session id")
    pptx_path = f"data/ppt/{session_id}.pptx"
    if not os.path.exists(pptx_path):
        raise HTTPException(404, "Presentation file not found on disk")

    slides = await _extract_slides(pptx_path)

    # Re-attach slide images if they exist on disk
    img_dir = f"data/ppt/slides/{session_id}"
    if os.path.isdir(img_dir):
        img_paths = sorted(
            glob.glob(os.path.join(img_dir, "slide-*.png")),
            key=lambda p: int("".join(filter(str.isdigit, os.path.basename(p))) or "0"),
        )
        version = int(os.path.getmtime(pptx_path) * 1000)
        for i, slide in enumerate(slides):
            if i < len(img_paths):
                fname = os.path.basename(img_paths[i])
                slide["image_url"] = f"/api/v1/ppt/image/{session_id}/{fname}?v={version}"

    # Restore in-memory state so navigate/jump work
    entry = next((e for e in _load_index() if e["session_id"] == session_id), {})
    title = entry.get("title", slides[0]["title"] if slides else "Presentation")
    _slide_store[session_id]  = slides
    _ppt_titles[session_id]   = title
    _current_slide[session_id] = 0

    global _latest_upload_sid
    _latest_upload_sid = session_id

    return {"status": "ok", "slide_count": len(slides), "slides": slides, "title": title}


# ── Ollama content generation ─────────────────────────────────────────────────

_GENERATE_PROMPT = """\
You are a professional presentation writer and subject matter expert.
Return ONLY valid JSON — no markdown, no explanation, nothing else.

Required format:
{{
  "presentation_title": "...",
  "slides": [
    {{
      "title": "...",
      "bullets": ["...", "...", "...", "...", "..."],
      "image_query": "2-4 word concrete visual search term"
    }},
    ...
  ]
}}

Rules:
- Generate exactly {n} slides
- First slide: compelling introduction with 5 bullets — context, why it matters, what will be covered
- Last slide: "Key Takeaways" with 5-6 actionable summary bullets
- Every content slide: 5-6 detailed bullet points
- Each bullet: a complete, informative sentence of 12-18 words — not just a label, explain the concept
- Titles: clear and descriptive, 4-7 words
- image_query: 2-4 concrete visual words for a stock photo search (e.g. "neural network diagram", "doctor hospital patient", "solar panels field", "team meeting office")
- No markdown symbols (* # -) inside bullet text
- No filler bullets — every point must add real knowledge or insight
- CRITICAL: Every slide MUST have a non-empty title AND exactly 5-6 bullets. An empty bullets array is strictly forbidden. If you have nothing to say on a topic, merge it with another slide instead.
- Output MUST be complete valid JSON

Topic: {topic}"""


def _repair_json(raw: str) -> str:
    """
    Best-effort repair for common LLM JSON quirks:
      - markdown code fences
      - // line comments (not valid JSON)
      - trailing commas before } or ]
      - missing comma between "value"\n"key" pairs
      - truncated output (model hit token limit mid-generation)
    """
    # Strip ```json ... ``` fences
    raw = re.sub(r'```[a-z]*\n?', '', raw).replace('```', '')
    # Remove // comments
    raw = re.sub(r'//[^\n]*', '', raw)
    # Remove trailing commas before ] or }
    raw = re.sub(r',\s*([\]}])', r'\1', raw)
    # Add missing commas between adjacent string values on consecutive lines
    raw = re.sub(r'"\s*\n(\s*)"', '",\n\\1"', raw)
    raw = raw.strip()

    # Truncation recovery: if we have more open braces/brackets than closed ones,
    # close them in reverse order so json.loads can at least parse what we got.
    depth = []
    PAIRS = {'{': '}', '[': ']'}
    in_str = False
    escape  = False
    for ch in raw:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in ('{', '['):
            depth.append(PAIRS[ch])
        elif ch in ('}', ']') and depth and depth[-1] == ch:
            depth.pop()

    # Strip any trailing partial token (e.g. `"bul`) before closing
    raw = re.sub(r',?\s*"[^"]*$', '', raw)  # remove trailing incomplete string
    raw = re.sub(r',\s*$', '', raw)          # remove trailing comma

    # Close all unclosed brackets in reverse
    raw += ''.join(reversed(depth))
    return raw


def _generate_content_sync(description: str, slide_count: int) -> dict | None:
    try:
        import ollama
        from core.config import settings
        prompt = _GENERATE_PROMPT.format(n=slide_count, topic=description)

        # format="json" enables grammar-constrained decoding — Ollama guarantees
        # the output is valid JSON regardless of model quirks.
        # think=False must be set to disable Qwen3 chain-of-thought (format+think
        # conflict was the original reason format="json" was avoided elsewhere).
        response = ollama.chat(
            model=settings.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            think=False,
            format="json",
            options={
                "num_predict": -1,          # no token cap — let model finish the JSON
                "num_ctx":     8192,        # enough context for 30-slide decks
                "temperature": 0.6,
            },
            stream=False,
        )
        raw = response.message.content if hasattr(response, "message") else response["message"]["content"]

        # format="json" guarantees valid JSON, but still validate the schema
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            # Should never happen with format="json", but try repair as last resort
            logger.warning(f"format=json still invalid ({e}), attempting repair")
            repaired = _repair_json(raw)
            data = json.loads(repaired)

        if "slides" not in data or not data["slides"]:
            logger.error(f"Response missing 'slides': {raw[:200]}")
            return None

        # Drop any slide Ollama generated with empty bullets — better to have
        # fewer slides than a blank slide in the deck.
        before = len(data["slides"])
        data["slides"] = [s for s in data["slides"] if s.get("bullets")]
        dropped = before - len(data["slides"])
        if dropped:
            logger.warning(f"{dropped} slide(s) had empty bullets and were removed")

        if not data["slides"]:
            logger.error("All slides had empty bullets after filtering")
            return None

        logger.info(f"Generated {len(data['slides'])} slides for: {description[:50]}")
        return data

    except Exception as e:
        logger.error(f"Ollama generate failed: {e}")
        return None


# ── Pexels image fetcher ──────────────────────────────────────────────────────

async def _fetch_pexels_image(query: str, api_key: str) -> bytes | None:
    """Fetch one landscape photo from Pexels for the given query. Returns raw bytes or None."""
    if not api_key or not query.strip():
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://api.pexels.com/v1/search",
                params={"query": query, "per_page": 3, "orientation": "landscape"},
                headers={"Authorization": api_key},
            )
            if resp.status_code != 200:
                logger.warning(f"Pexels search {resp.status_code} for '{query}'")
                return None
            photos = resp.json().get("photos", [])
            if not photos:
                return None
            img_url = photos[0]["src"]["medium"]
            img_resp = await client.get(img_url, timeout=12.0)
            if img_resp.status_code == 200:
                return img_resp.content
    except Exception as e:
        logger.warning(f"Pexels fetch failed for '{query}': {e}")
    return None


# ── python-pptx builder ───────────────────────────────────────────────────────

def _build_pptx(content: dict, out_path: str, images: dict[int, bytes] | None = None):
    import random
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN

    # Curated professional dark-theme accent palettes — one picked randomly per deck
    PALETTES = [
        (RGBColor(0xF5, 0xA7, 0x00), RGBColor(0x0F, 0x0F, 0x1A)),  # amber / midnight
        (RGBColor(0x38, 0xBD, 0xF8), RGBColor(0x0C, 0x14, 0x23)),  # sky blue / deep navy
        (RGBColor(0x34, 0xD3, 0x99), RGBColor(0x0A, 0x1A, 0x14)),  # emerald / dark green
        (RGBColor(0xA7, 0x8B, 0xFA), RGBColor(0x10, 0x0A, 0x24)),  # violet / deep purple
        (RGBColor(0xFB, 0x71, 0x85), RGBColor(0x1F, 0x0A, 0x10)),  # rose / dark maroon
        (RGBColor(0xFB, 0xBF, 0x24), RGBColor(0x18, 0x12, 0x02)),  # gold / rich dark
        (RGBColor(0x22, 0xD3, 0xEE), RGBColor(0x04, 0x16, 0x1A)),  # cyan / deep teal
        (RGBColor(0xFF, 0x7B, 0x54), RGBColor(0x1A, 0x0C, 0x06)),  # coral / dark brown
    ]
    ACCENT, DARK = random.choice(PALETTES)
    WHITE = RGBColor(0xFF, 0xFF, 0xFF)
    LGRAY = RGBColor(0xCC, 0xCC, 0xCC)
    DIM   = RGBColor(0x55, 0x55, 0x55)

    prs = Presentation()
    prs.slide_width  = Inches(13.33)
    prs.slide_height = Inches(7.5)
    blank_layout = prs.slide_layouts[6]

    slides_data = content.get("slides", [])
    prs_title   = content.get("presentation_title", "Presentation")

    for idx, slide_data in enumerate(slides_data):
        slide = prs.slides.add_slide(blank_layout)

        bg = slide.background.fill
        bg.solid()
        bg.fore_color.rgb = DARK

        title_text = slide_data.get("title", "")
        bullets    = slide_data.get("bullets", [])
        is_first   = idx == 0

        # Derive title from first bullet if Ollama returned empty title
        if not title_text.strip():
            if bullets:
                words = bullets[0].split()
                title_text = " ".join(words[:6]).rstrip(".,;:")
            else:
                title_text = f"Slide {idx + 1}"

        if is_first:
            # ── Cover slide ──────────────────────────────────────────────────
            # Thin accent strip at top (matches content slides)
            strip = slide.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.13))
            strip.fill.solid(); strip.fill.fore_color.rgb = ACCENT
            strip.line.fill.background()

            # Title — left-aligned, same left margin as content slides
            tb = slide.shapes.add_textbox(Inches(0.7), Inches(0.5), Inches(12.0), Inches(1.8))
            tf = tb.text_frame; tf.word_wrap = True
            p  = tf.paragraphs[0]; p.alignment = PP_ALIGN.LEFT
            run = p.add_run(); run.text = prs_title
            run.font.size = Pt(36); run.font.bold = True; run.font.color.rgb = WHITE

            # Accent divider bar — fixed position below title area
            bar = slide.shapes.add_shape(1, Inches(0.7), Inches(2.55), Inches(11.9), Inches(0.045))
            bar.fill.solid(); bar.fill.fore_color.rgb = ACCENT
            bar.line.fill.background()

            # Bullets — same left margin as title
            if bullets:
                tb2 = slide.shapes.add_textbox(Inches(0.7), Inches(2.75), Inches(12.0), Inches(4.5))
                tf2 = tb2.text_frame; tf2.word_wrap = True
                for i, b in enumerate(bullets[:6]):
                    para = tf2.paragraphs[0] if i == 0 else tf2.add_paragraph()
                    para.alignment = PP_ALIGN.LEFT
                    para.space_before = Pt(10)
                    r = para.add_run()
                    r.text = f"{i + 1}.  {b}"
                    r.font.size = Pt(17); r.font.color.rgb = LGRAY
        else:
            # ── Content slide ────────────────────────────────────────────────
            img_bytes = (images or {}).get(idx)

            strip = slide.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.13))
            strip.fill.solid(); strip.fill.fore_color.rgb = ACCENT
            strip.line.fill.background()

            # Title spanning full width regardless of image
            tb = slide.shapes.add_textbox(Inches(0.5), Inches(0.28), Inches(12.33), Inches(1.1))
            tf = tb.text_frame; tf.word_wrap = True
            p  = tf.paragraphs[0]
            run = p.add_run(); run.text = title_text
            run.font.size = Pt(29); run.font.bold = True; run.font.color.rgb = WHITE

            if img_bytes:
                # ── Two-column: bullets left 57 %, image right 40 % ──────────
                bullet_w = Inches(7.0)
                tb2 = slide.shapes.add_textbox(Inches(0.5), Inches(1.55), bullet_w, Inches(5.7))
                tf2 = tb2.text_frame; tf2.word_wrap = True
                for i, b in enumerate(bullets[:6]):
                    para = tf2.paragraphs[0] if i == 0 else tf2.add_paragraph()
                    para.alignment = PP_ALIGN.LEFT
                    para.space_before = Pt(9)
                    r = para.add_run()
                    r.text = f"{i + 1}.  {b}"
                    r.font.size = Pt(17); r.font.color.rgb = LGRAY

                # Image: right column with a small inset from edges
                from io import BytesIO
                try:
                    slide.shapes.add_picture(
                        BytesIO(img_bytes),
                        Inches(7.8), Inches(1.5), Inches(5.1), Inches(5.6),
                    )
                except Exception as e:
                    logger.warning(f"Slide {idx+1} image insert failed: {e}")
            else:
                # ── Full-width bullets (no image / fetch failed) ──────────────
                tb2 = slide.shapes.add_textbox(Inches(0.6), Inches(1.55), Inches(12.1), Inches(5.7))
                tf2 = tb2.text_frame; tf2.word_wrap = True
                for i, b in enumerate(bullets[:6]):
                    para = tf2.paragraphs[0] if i == 0 else tf2.add_paragraph()
                    para.alignment = PP_ALIGN.LEFT
                    para.space_before = Pt(10)
                    r = para.add_run()
                    r.text = f"{i + 1}.  {b}"
                    r.font.size = Pt(18); r.font.color.rgb = LGRAY

            # Slide number bottom-right
            num_tb = slide.shapes.add_textbox(Inches(12.0), Inches(7.0), Inches(1.2), Inches(0.4))
            ntf = num_tb.text_frame
            np_ = ntf.paragraphs[0]; np_.alignment = PP_ALIGN.RIGHT
            nr  = np_.add_run(); nr.text = str(idx + 1)
            nr.font.size = Pt(10); nr.font.color.rgb = DIM

    prs.save(out_path)
    logger.info(f"python-pptx built {len(slides_data)} slides → {out_path} (accent={ACCENT})")


async def _extract_slides(path: str) -> list[dict]:
    """Extract slide titles from pptx using python-pptx."""
    try:
        import asyncio
        return await asyncio.to_thread(_extract_sync, path)
    except Exception as e:
        return [{"index": i, "title": f"Slide {i+1}", "notes": ""} for i in range(10)]


def _extract_sync(path: str) -> list[dict]:
    from pptx import Presentation

    prs = Presentation(path)
    slides = []

    # Standard PPTX slide EMU dimensions
    SLIDE_W = int(prs.slide_width)  or 9144000
    SLIDE_H = int(prs.slide_height) or 6858000

    for i, slide in enumerate(prs.slides):
        # --- Background color ---
        bg_color = "#111111"
        try:
            fill = slide.background.fill
            if fill.type is not None:
                try:
                    rgb = fill.fore_color.rgb
                    bg_color = f"#{rgb.r:02X}{rgb.g:02X}{rgb.b:02X}"
                except Exception:
                    pass
        except Exception:
            pass

        # --- Shapes: text + styling + position ---
        title = ""
        body  = ""
        shapes_data: list[dict] = []

        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            text = shape.text_frame.text.strip()
            if not text:
                continue

            text_color = "#FFFFFF"
            font_size  = 24.0
            is_bold    = False
            s_align    = "left"

            try:
                for para in shape.text_frame.paragraphs:
                    if not para.text.strip():
                        continue
                    # Text alignment from first non-empty paragraph
                    try:
                        from pptx.enum.text import PP_ALIGN
                        a = para.alignment
                        if a == PP_ALIGN.CENTER:      s_align = "center"
                        elif a == PP_ALIGN.RIGHT:     s_align = "right"
                        elif a == PP_ALIGN.DISTRIBUTE: s_align = "justify"
                    except Exception:
                        pass
                    for run in para.runs:
                        try:
                            if run.font.color.type is not None:
                                rgb = run.font.color.rgb
                                text_color = f"#{rgb.r:02X}{rgb.g:02X}{rgb.b:02X}"
                        except Exception:
                            pass
                        try:
                            if run.font.size is not None:
                                font_size = run.font.size.pt
                        except Exception:
                            pass
                        try:
                            if run.font.bold is not None:
                                is_bold = run.font.bold
                        except Exception:
                            pass
                        break
                    break
            except Exception:
                pass

            # Position as percentage of slide dimensions
            try:
                s_left  = round((shape.left  or 0) / SLIDE_W * 100, 2)
                s_top   = round((shape.top   or 0) / SLIDE_H * 100, 2)
                s_width = round((shape.width or SLIDE_W) / SLIDE_W * 100, 2)
            except Exception:
                s_left, s_top, s_width = 0.0, 0.0, 90.0

            shapes_data.append({
                "text":  text,
                "color": text_color,
                "size":  min(float(font_size), 80.0),
                "bold":  is_bold,
                "left":  s_left,
                "top":   s_top,
                "width": s_width,
                "align": s_align,
            })

            is_title = "title" in shape.name.lower() or (not title and font_size >= 28)
            if is_title and not title:
                title = text
            else:
                body += text[:120] + " "

        if not title and shapes_data:
            title = shapes_data[0]["text"]
        if not title:
            title = f"Slide {i+1}"

        slides.append({
            "index":    i,
            "title":    title,
            "notes":    body.strip()[:200],
            "bg_color": bg_color,
            "shapes":   shapes_data[:15],
        })

    return slides
