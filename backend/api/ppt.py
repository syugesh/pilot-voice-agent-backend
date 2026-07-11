"""PPT API — navigate + file upload + AI generation with slide extraction."""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
import os, json, asyncio, subprocess, shutil, glob, logging, time, re, tempfile, json
from io import BytesIO

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
_slide_version_store: dict[str, dict[int, list[dict]]] = {}  # session_id → slide_index → previous versions
_ppt_render_locks: dict[str, asyncio.Lock] = {}

_INDEX_PATH = "data/ppt/index.json"


def _load_index() -> list[dict]:
    try:
        if os.path.exists(_INDEX_PATH):
            with open(_INDEX_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _kinds_path(session_id: str) -> str:
    return f"data/ppt/{session_id}.kinds.json"


def _save_kinds(session_id: str, kinds: list):
    """Persist the per-slide template 'kind' list (e.g. "team", "table", or
    None for slides with no known kind) alongside a generated deck. A .pptx
    file has no field for "this slide is a team slide" — this sidecar is
    what lets editing be kind-aware after the fact, since _slide_store is
    rebuilt from scratch by re-reading the file on every load."""
    os.makedirs("data/ppt", exist_ok=True)
    with open(_kinds_path(session_id), "w") as f:
        json.dump(kinds, f)


def _load_kinds(session_id: str) -> list | None:
    try:
        path = _kinds_path(session_id)
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _sources_path(session_id: str) -> str:
    return f"data/ppt/{session_id}.sources.json"


def _save_sources(session_id: str, sources: list):
    """Persist which exact candidate slide (of possibly several for that
    kind — see _KIND_SOURCES in ppt_template_builder.py) each slide was
    cloned from, as "template_path::index" strings aligned 1:1 with the
    kinds sidecar. Different candidates for the same kind don't share
    shape_ids, so editing a slide later needs to know precisely which one
    it came from to compute the matching slot map — just knowing the kind
    isn't enough once a kind can be built from more than one template slide.
    """
    os.makedirs("data/ppt", exist_ok=True)
    with open(_sources_path(session_id), "w") as f:
        json.dump(sources, f)


def _load_sources(session_id: str) -> list | None:
    try:
        path = _sources_path(session_id)
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return None


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


def _convert_to_images_sync(pptx_path: str, out_dir: str, keep_existing_on_failure: bool = True) -> list[str]:
    """Convert every slide to PNGs without deleting the last good render first."""
    soffice = _find_soffice()
    if not soffice:
        existing = sorted(
            glob.glob(os.path.join(out_dir, "slide-*.png")),
            key=lambda p: int("".join(filter(str.isdigit, os.path.basename(p))) or "0"),
        )
        return existing if keep_existing_on_failure else []
    os.makedirs(out_dir, exist_ok=True)

    existing_images = sorted(
        glob.glob(os.path.join(out_dir, "slide-*.png")),
        key=lambda p: int("".join(filter(str.isdigit, os.path.basename(p))) or "0"),
    )
    work_dir = tempfile.mkdtemp(prefix="ppt-render-")

    try:
        # Step 1: PPTX → PDF — LibreOffice produces a faithful multi-page PDF.
        r = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", work_dir, pptx_path],
            capture_output=True, text=True, timeout=120,
        )
        basename = os.path.splitext(os.path.basename(pptx_path))[0]
        pdf_path = os.path.join(work_dir, f"{basename}.pdf")
        if r.returncode != 0 or not os.path.exists(pdf_path):
            logger.error(f"LibreOffice PDF conversion failed: {r.stderr[:300]}")
            return existing_images if keep_existing_on_failure else []

        # Step 2: PDF pages → PNGs via pdftoppm (installed via: brew install poppler).
        pdftoppm = _find_pdftoppm()
        if not pdftoppm:
            logger.warning("pdftoppm not found — install poppler: brew install poppler")
            return existing_images if keep_existing_on_failure else []

        prefix = os.path.join(work_dir, "slide")
        r2 = subprocess.run(
            [pdftoppm, "-png", "-r", "150", pdf_path, prefix],
            capture_output=True, text=True, timeout=120,
        )
        if r2.returncode != 0:
            logger.error(f"pdftoppm failed: {r2.stderr[:300]}")
            return existing_images if keep_existing_on_failure else []

        rendered = sorted(
            glob.glob(os.path.join(work_dir, "slide-*.png")),
            key=lambda p: int("".join(filter(str.isdigit, os.path.basename(p))) or "0"),
        )
        if not rendered:
            logger.error("PDF conversion produced no slide images")
            return existing_images if keep_existing_on_failure else []

        for stale in glob.glob(os.path.join(out_dir, "slide-*.png")) + glob.glob(os.path.join(out_dir, "*.pdf")):
            try:
                os.remove(stale)
            except OSError:
                pass

        final_images: list[str] = []
        for i, src in enumerate(rendered, start=1):
            dst = os.path.join(out_dir, f"slide-{i}.png")
            shutil.move(src, dst)
            final_images.append(dst)

        logger.info(f"Converted {len(final_images)} slides to PNG")
        return final_images
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


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
    img_paths = await asyncio.to_thread(_convert_to_images_sync, path, img_dir, False)

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
    Uses Ollama (local, free) to write kind-tagged slide content, then clones
    matching slides out of the real Grid Dynamics template and swaps in that
    content — see services/ppt_template_builder.py.
    Returns the same slide format as /upload so the viewer loads immediately.
    """
    if not req.description.strip():
        raise HTTPException(400, "Description cannot be empty")
    slide_count = max(3, min(req.slide_count, 20))

    # Step 1 — generate kind-tagged slide content with Ollama
    from services.ppt_template_builder import generate_template_content, build_deck_from_template
    content = await generate_template_content(req.description, slide_count)
    if not content:
        raise HTTPException(502, "Ollama content generation failed — is Ollama running?")

    # Step 2 — clone the matching GD template slides and populate them
    os.makedirs("data/ppt", exist_ok=True)
    pptx_path = f"data/ppt/{req.session_id}.pptx"
    sources = await asyncio.to_thread(build_deck_from_template, content, pptx_path)
    _save_kinds(req.session_id, [s.get("kind") for s in content["slides"]])
    _save_sources(req.session_id, sources)

    # Step 3 — convert to slide images if LibreOffice is available (same as upload)
    img_dir   = f"data/ppt/slides/{req.session_id}"
    img_paths = await asyncio.to_thread(_convert_to_images_sync, pptx_path, img_dir, False)

    # Step 4 — extract metadata into the same format the viewer expects
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


@router.get("/export-pdf/{session_id}")
async def export_pdf(session_id: str):
    """Export the presentation as a PDF using LibreOffice (same pipeline as slide image generation)."""
    if re.search(r'[/\\.]\.', session_id):
        raise HTTPException(400, "Invalid session id")
    pptx_path = f"data/ppt/{session_id}.pptx"
    if not os.path.exists(pptx_path):
        raise HTTPException(404, "No presentation found for this session")

    soffice = _find_soffice()
    if not soffice:
        raise HTTPException(503, "LibreOffice not installed — cannot export PDF")

    raw_title = _ppt_titles.get(session_id) or "presentation"
    safe_name = re.sub(r'[^\w\s-]', '', raw_title)[:60].strip().replace(' ', '_') or "presentation"

    # Convert in a temp dir so concurrent exports don't collide
    tmp_dir = tempfile.mkdtemp(prefix="pilot_pdf_")
    try:
        r = await asyncio.to_thread(
            subprocess.run,
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp_dir, pptx_path],
            capture_output=True, text=True, timeout=120,
        )
        basename = os.path.splitext(os.path.basename(pptx_path))[0]
        pdf_path = os.path.join(tmp_dir, f"{basename}.pdf")
        if r.returncode != 0 or not os.path.exists(pdf_path):
            logger.error(f"LibreOffice PDF export failed: {r.stderr[:300]}")
            raise HTTPException(502, "PDF export failed")
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=f"{safe_name}.pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
            background=None,  # keep file alive during streaming
        )
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.error(f"PDF export error: {e}")
        raise HTTPException(502, f"PDF export error: {e}")


class EditSlideReq(BaseModel):
    session_id:  str
    slide_index: int
    title:       str
    bullets:     List[str]
    notes:       Optional[str] = ""
    # Optional map of shape_id (as string) -> new text, for editing any other
    # shape on the slide (subtitle, caption, footer, etc.) beyond the title
    # and numbered-bullet body. Defaults to None = no other shapes touched.
    other_edits: Optional[dict] = None


def _extract_slide_bullets(slide: dict) -> list[str]:
    bullets: list[str] = []
    for sh in slide.get("shapes", []):
        for line in sh.get("text", "").split("\n"):
            m = re.match(r'^\d+\.\s{1,3}(.+)', line)
            if m:
                bullets.append(m.group(1).strip())
    return bullets


def _remember_slide_version(session_id: str, slide_index: int, slide: dict) -> dict:
    """Capture the editable slide fields before an AI/manual edit mutates them."""
    version = {
        "id": None,
        "title": slide.get("title", ""),
        "bullets": _extract_slide_bullets(slide),
        "notes": slide.get("notes", ""),
        "created_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    _slide_version_store.setdefault(session_id, {}).setdefault(slide_index, []).insert(0, version)
    return version


@router.patch("/slide")
async def edit_slide(req: EditSlideReq):
    """
    Edit the content of a specific slide:
      - Updates title, bullet text, and speaker notes in the .pptx on disk
      - Re-renders slide thumbnails (full deck via LibreOffice)
      - Updates the in-memory _slide_store so the viewer reflects changes immediately
    """
    if re.search(r'[/\\.]\.', req.session_id):
        raise HTTPException(400, "Invalid session id")
    pptx_path = f"data/ppt/{req.session_id}.pptx"
    if not os.path.exists(pptx_path):
        raise HTTPException(404, "No presentation found for this session")

    def _writer(slide):
        _patch_slide(slide, req.slide_index, req.title, req.bullets, req.notes or "", req.other_edits)

    slides = await apply_slide_edit_async(req.session_id, req.slide_index, _writer)
    if slides is None:
        raise HTTPException(400, f"Slide index {req.slide_index} out of range")

    return {"status": "ok", "slides": slides}


@router.get("/kinds")
async def list_kinds():
    """All addable slide kinds + their field schema, for the 'Add Slide' kind
    picker — unlike GET /slide/{sid}/{idx}/schema, this isn't scoped to an
    existing slide (there isn't one yet)."""
    from services.ppt_template_builder import KIND_FIELD_SCHEMA
    # "cover" and "thank_you" are structural (auto-added at generation time,
    # exactly one of each) — not offered as a repeatable "add another" kind.
    addable = {k: v for k, v in KIND_FIELD_SCHEMA.items() if k not in ("cover", "thank_you")}
    return {"kinds": addable}


@router.get("/slide/{session_id}/{slide_index}/schema")
async def get_slide_schema(session_id: str, slide_index: int):
    """
    Kind-aware edit schema for one slide: the field list the frontend needs
    to render an edit form (e.g. a repeatable name/role list for a "team"
    slide, an editable grid for a "table" slide) plus that slide's current
    values. Slides with no known kind (uploaded/legacy decks — see
    _load_kinds) return {"kind": null}; the frontend falls back to the
    existing generic title/bullets/notes form in that case, unchanged.
    """
    if re.search(r'[/\\.]\.', session_id):
        raise HTTPException(400, "Invalid session id")
    pptx_path = f"data/ppt/{session_id}.pptx"
    if not os.path.exists(pptx_path):
        raise HTTPException(404, "No presentation found for this session")

    kinds = _load_kinds(session_id)
    kind = kinds[slide_index] if kinds and 0 <= slide_index < len(kinds) else None
    if not kind:
        return {"kind": None}

    sources = _load_sources(session_id)
    source = sources[slide_index] if sources and 0 <= slide_index < len(sources) else None

    from services.ppt_template_builder import extract_slide_data, KIND_FIELD_SCHEMA, _parse_source

    def _read():
        from pptx import Presentation
        prs = Presentation(pptx_path)
        if slide_index >= len(prs.slides):
            return None
        return extract_slide_data(prs.slides[slide_index], kind, _parse_source(source))

    data = await asyncio.to_thread(_read)
    if data is None:
        raise HTTPException(400, f"Slide index {slide_index} out of range")

    return {"kind": kind, "fields": KIND_FIELD_SCHEMA.get(kind, []), "data": data}


class EditSlideKindReq(BaseModel):
    session_id:  str
    slide_index: int
    kind:        str
    data:        dict
    # Notes are a separate field on every kind (not part of any slot map) —
    # written directly to the notes slide, never through the generic
    # title/bullet-shape heuristic in _patch_slide, which is exactly what
    # this whole kind-aware path exists to avoid running against these slides.
    notes: Optional[str] = None


@router.patch("/slide/kind")
async def edit_slide_kind(req: EditSlideKindReq):
    """Kind-aware counterpart to PATCH /slide — writes structured field data
    (team members, table rows, comparison columns, ...) via the same
    slot-map populate functions used at generation time. See
    services/ppt_template_builder.py."""
    if re.search(r'[/\\.]\.', req.session_id):
        raise HTTPException(400, "Invalid session id")
    pptx_path = f"data/ppt/{req.session_id}.pptx"
    if not os.path.exists(pptx_path):
        raise HTTPException(404, "No presentation found for this session")

    from services.ppt_template_builder import populate_slide_data, _parse_source

    sources = _load_sources(req.session_id)
    source = sources[req.slide_index] if sources and 0 <= req.slide_index < len(sources) else None

    def _writer(slide):
        populate_slide_data(slide, req.kind, req.data, _parse_source(source))
        if req.notes is not None:
            try:
                notes_tf = slide.notes_slide.notes_text_frame
                notes_tf.clear()
                para = notes_tf.paragraphs[0] if notes_tf.paragraphs else notes_tf.add_paragraph()
                para.add_run().text = req.notes
            except Exception as e:
                logger.warning(f"Could not write speaker notes for slide {req.slide_index}: {e}")

    slides = await apply_slide_edit_async(req.session_id, req.slide_index, _writer)
    if slides is None:
        raise HTTPException(400, f"Slide index {req.slide_index} out of range")

    return {"status": "ok", "slides": slides}


def _apply_writer_sync(pptx_path: str, slide_index: int, writer):
    from pptx import Presentation
    prs = Presentation(pptx_path)
    if slide_index >= len(prs.slides):
        raise ValueError(f"Slide {slide_index} does not exist in the file")
    writer(prs.slides[slide_index])
    prs.save(pptx_path)


async def apply_slide_edit_fast_async(session_id: str, slide_index: int, writer) -> bool:
    """
    Records a version snapshot and writes `writer(slide)` to the .pptx file
    on disk — the fast part of an edit (well under a second). Does NOT
    regenerate thumbnails or refresh _slide_store; call
    refresh_slide_thumbnails_async afterward for that.

    Split out of what's now apply_slide_edit_async below so voice tools can
    treat thumbnail regeneration as a backgroundable step instead of part of
    what the user has to wait through before hearing a spoken reply —
    LibreOffice re-rendering the whole deck (process startup + full-deck
    render) on every single-field voice edit was adding several seconds to
    every "I've updated slide N" confirmation, even though the file itself
    is already correctly written well before that finishes.
    Returns True on success, False if the session/slide index is invalid.
    """
    pptx_path = f"data/ppt/{session_id}.pptx"
    if not os.path.exists(pptx_path):
        return False

    slides_mem = _slide_store.get(session_id, [])
    if slide_index < 0 or slide_index >= len(slides_mem):
        return False

    # Record the previous editable content before applying the mutation. The
    # in-memory copy satisfies session-duration history even if SQLite is not
    # available; the DB write below preserves the same record across reloads.
    # This snapshot is title/bullets/notes-shaped regardless of the writer —
    # harmless for kind-aware edits (bullets just come back empty, since
    # kind-aware slides don't use the numbered-bullet convention), just a
    # less detailed "before" record in the version-diff UI for those.
    old_slide = slides_mem[slide_index]
    old_version = _remember_slide_version(session_id, slide_index, old_slide)

    try:
        from db.engine import AsyncSessionLocal
        from db.models import PPTSlideVersion
        async with AsyncSessionLocal() as db_session:
            v = PPTSlideVersion(
                session_id=session_id,
                slide_index=slide_index,
                title=old_version["title"],
                bullets=json.dumps(old_version["bullets"]),
                notes=old_version["notes"]
            )
            db_session.add(v)
            await db_session.commit()
    except Exception as e:
        logger.error(f"Failed to record slide version: {e}")

    await asyncio.to_thread(_apply_writer_sync, pptx_path, slide_index, writer)
    return True


async def refresh_slide_thumbnails_async(session_id: str) -> Optional[List[dict]]:
    """Re-renders thumbnails (LibreOffice, full deck) and refreshes
    _slide_store from the current .pptx on disk — the slow part of an edit,
    split out so voice tools can run it as a background task after already
    replying. Returns the updated slides list, or None if the file is missing."""
    pptx_path = f"data/ppt/{session_id}.pptx"
    if not os.path.exists(pptx_path):
        return None

    lock = _ppt_render_locks.setdefault(session_id, asyncio.Lock())
    async with lock:
        img_dir   = f"data/ppt/slides/{session_id}"
        img_paths = await asyncio.to_thread(_convert_to_images_sync, pptx_path, img_dir)

        slides = await _extract_slides(pptx_path)
        version = int(time.time() * 1000)
        for i, slide in enumerate(slides):
            if i < len(img_paths):
                fname = os.path.basename(img_paths[i])
                slide["image_url"] = f"/api/v1/ppt/image/{session_id}/{fname}?v={version}"

        _slide_store[session_id] = slides
        return slides


async def apply_slide_edit_async(
    session_id: str, slide_index: int, writer,
) -> Optional[List[dict]]:
    """
    Applies a mutation to one slide via `writer(slide)` — an already-open,
    not-yet-saved slide — then synchronously regenerates thumbnails and
    updates memory state. Used by the HTTP PATCH endpoints (PATCH /slide,
    PATCH /slide/kind), whose caller (the Edit modal) is already showing a
    "Saving…" spinner and needs the updated slides list back in the same
    response. Voice tools use apply_slide_edit_fast_async +  a backgrounded
    refresh_slide_thumbnails_async instead, to reply as soon as the file
    write succeeds rather than waiting for a full-deck LibreOffice
    re-render too — see tools/ppt_copilot.py.
    Returns the updated slides list, or None if invalid index.
    """
    ok = await apply_slide_edit_fast_async(session_id, slide_index, writer)
    if not ok:
        return None
    return await refresh_slide_thumbnails_async(session_id)


# ── WYSIWYG canvas geometry write ────────────────────────────────────────────

class ShapeGeometry(BaseModel):
    shape_id: int
    # All percentages of the slide (0–100), matching what _extract_sync emits.
    # Any field omitted (None) is left unchanged on that shape.
    left:   Optional[float] = None
    top:    Optional[float] = None
    width:  Optional[float] = None
    height: Optional[float] = None
    rotation: Optional[float] = None


class SlideGeometryReq(BaseModel):
    session_id:  str
    slide_index: int
    shapes:      List[ShapeGeometry]   # batch: a drag+resize can move several at once


def _write_geometry_sync(pptx_path: str, slide_index: int, shapes: List[ShapeGeometry]):
    """Write shape position/size/rotation to the .pptx. Percent → EMU using the
    real slide dimensions, keyed by the stable shape_id (never array index, so
    a canvas edit can't hit the wrong shape). Unknown shape_ids are skipped."""
    from pptx import Presentation
    from pptx.util import Emu
    from services.ppt_template_builder import _shape_by_id

    prs = Presentation(pptx_path)
    if slide_index >= len(prs.slides):
        raise ValueError(f"Slide {slide_index} does not exist in the file")
    slide = prs.slides[slide_index]
    sw, sh = int(prs.slide_width), int(prs.slide_height)

    for g in shapes:
        shape = _shape_by_id(slide, g.shape_id)
        if shape is None:
            logger.warning(f"geometry: shape_id {g.shape_id} not found on slide {slide_index}")
            continue
        if g.left   is not None: shape.left   = Emu(int(sw * g.left   / 100))
        if g.top    is not None: shape.top    = Emu(int(sh * g.top    / 100))
        if g.width  is not None: shape.width  = Emu(int(sw * g.width  / 100))
        if g.height is not None: shape.height = Emu(int(sh * g.height / 100))
        if g.rotation is not None:
            try:
                shape.rotation = float(g.rotation)
            except Exception:
                pass  # not all shape types support rotation
    prs.save(pptx_path)


async def apply_shape_geometry_async(session_id: str, slide_index: int,
                                      shapes: List[ShapeGeometry]) -> Optional[List[dict]]:
    """Fast geometry write + thumbnail refresh, mirroring apply_slide_edit_async."""
    pptx_path = f"data/ppt/{session_id}.pptx"
    if not os.path.exists(pptx_path):
        return None
    slides_mem = _slide_store.get(session_id, [])
    if slide_index < 0 or slide_index >= len(slides_mem):
        return None
    await asyncio.to_thread(_write_geometry_sync, pptx_path, slide_index, shapes)
    return await refresh_slide_thumbnails_async(session_id)


class ShapeTextReq(BaseModel):
    session_id:  str
    slide_index: int
    shape_id:    int
    text:        str


@router.patch("/slide/shape-text")
async def edit_shape_text(req: ShapeTextReq):
    """Set one shape's text by shape_id — the inline-text-edit path behind the
    WYSIWYG canvas. Reuses _set_shape_text (preserves the shape's existing
    font styling) rather than the title/bullets-shaped PATCH /slide."""
    if re.search(r'[/\\.]\.', req.session_id):
        raise HTTPException(400, "Invalid session id")
    pptx_path = f"data/ppt/{req.session_id}.pptx"
    if not os.path.exists(pptx_path):
        raise HTTPException(404, "No presentation found for this session")

    from services.ppt_template_builder import _shape_by_id

    def _writer(slide):
        shape = _shape_by_id(slide, req.shape_id)
        if shape is not None and shape.has_text_frame:
            _set_shape_text(shape, req.text)

    slides = await apply_slide_edit_async(req.session_id, req.slide_index, _writer)
    if slides is None:
        raise HTTPException(400, f"Slide index {req.slide_index} out of range")
    return {"status": "ok", "slides": slides}


def _replace_picture_sync(pptx_path: str, slide_index: int, shape_id: int, image_bytes: bytes):
    from pptx import Presentation
    from services.ppt_template_builder import _shape_by_id

    prs = Presentation(pptx_path)
    if slide_index >= len(prs.slides):
        raise ValueError(f"Slide {slide_index} does not exist in the file")

    slide = prs.slides[slide_index]
    shape = _shape_by_id(slide, shape_id)
    if shape is None:
        raise ValueError(f"Shape {shape_id} does not exist on slide {slide_index}")

    left, top, width, height = shape.left, shape.top, shape.width, shape.height
    try:
        rotation = float(shape.rotation or 0)
    except Exception:
        rotation = 0

    # python-pptx has no public "replace image" API. Remove the selected
    # picture-like shape and insert the uploaded bitmap at the same geometry.
    shape._element.getparent().remove(shape._element)
    new_shape = slide.shapes.add_picture(BytesIO(image_bytes), left, top, width=width, height=height)
    try:
        new_shape.rotation = rotation
    except Exception:
        pass
    prs.save(pptx_path)


@router.patch("/slide/shape-image")
async def edit_shape_image(
    session_id: str = Form(...),
    slide_index: int = Form(...),
    shape_id: int = Form(...),
    file: UploadFile = File(...),
):
    """Replace a picture shape from the WYSIWYG canvas, then re-render the
    LibreOffice preview so the user sees the actual deck output."""
    if re.search(r'[/\\.]\.', session_id):
        raise HTTPException(400, "Invalid session id")
    pptx_path = f"data/ppt/{session_id}.pptx"
    if not os.path.exists(pptx_path):
        raise HTTPException(404, "No presentation found for this session")

    content_type = (file.content_type or "").lower()
    if content_type and not content_type.startswith("image/"):
        raise HTTPException(400, "Only image uploads are supported")
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(400, "Uploaded image is empty")

    try:
        await asyncio.to_thread(_replace_picture_sync, pptx_path, slide_index, shape_id, image_bytes)
    except ValueError as e:
        raise HTTPException(400, str(e))

    slides = await refresh_slide_thumbnails_async(session_id)
    if slides is None:
        raise HTTPException(400, f"Slide index {slide_index} out of range")
    return {"status": "ok", "slides": slides}


class ShapeStyleReq(BaseModel):
    session_id:  str
    slide_index: int
    shape_id:    int
    # All optional — only fields present are changed, matching ShapeGeometry's
    # partial-update convention so the toolbar can send just what the user toggled.
    bold:      Optional[bool]  = None
    italic:    Optional[bool]  = None
    underline: Optional[bool]  = None
    font:      Optional[str]   = None
    size:      Optional[float] = None
    color:     Optional[str]   = None   # "#RRGGBB"
    align:     Optional[str]   = None   # left | center | right | justify


def _apply_run_style(run, req: "ShapeStyleReq"):
    from pptx.util import Pt
    from pptx.dml.color import RGBColor
    if req.bold is not None:
        run.font.bold = req.bold
    if req.italic is not None:
        run.font.italic = req.italic
    if req.underline is not None:
        run.font.underline = req.underline
    if req.font is not None:
        run.font.name = req.font
    if req.size is not None:
        run.font.size = Pt(req.size)
    if req.color is not None:
        hexcolor = req.color.lstrip("#")
        if len(hexcolor) == 6:
            run.font.color.rgb = RGBColor.from_string(hexcolor.upper())


def _write_shape_style_sync(pptx_path: str, slide_index: int, req: "ShapeStyleReq"):
    from pptx import Presentation
    from pptx.enum.text import PP_ALIGN
    from services.ppt_template_builder import _shape_by_id

    prs = Presentation(pptx_path)
    if slide_index >= len(prs.slides):
        raise ValueError(f"Slide {slide_index} does not exist in the file")
    slide = prs.slides[slide_index]
    shape = _shape_by_id(slide, req.shape_id)
    if shape is None or not shape.has_text_frame:
        raise ValueError(f"Shape {req.shape_id} does not exist or has no text on slide {slide_index}")

    align_map = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
                 "right": PP_ALIGN.RIGHT, "justify": PP_ALIGN.DISTRIBUTE}

    for para in shape.text_frame.paragraphs:
        if req.align is not None and req.align in align_map:
            para.alignment = align_map[req.align]
        for run in para.runs:
            _apply_run_style(run, req)
    prs.save(pptx_path)


@router.patch("/slide/shape-style")
async def edit_shape_style(req: ShapeStyleReq):
    """Apply text formatting (bold/italic/underline/font/size/color/align) to
    every run in a shape — the write path behind the canvas toolbar."""
    if re.search(r'[/\\.]\.', req.session_id):
        raise HTTPException(400, "Invalid session id")
    pptx_path = f"data/ppt/{req.session_id}.pptx"
    if not os.path.exists(pptx_path):
        raise HTTPException(404, "No presentation found for this session")

    try:
        await asyncio.to_thread(_write_shape_style_sync, pptx_path, req.slide_index, req)
    except ValueError as e:
        raise HTTPException(400, str(e))

    slides = await refresh_slide_thumbnails_async(req.session_id)
    if slides is None:
        raise HTTPException(400, f"Slide index {req.slide_index} out of range")
    return {"status": "ok", "slides": slides}


class AddTextBoxReq(BaseModel):
    session_id:  str
    slide_index: int
    text:        str = "New text"
    left:   float = 35.0
    top:    float = 40.0
    width:  float = 30.0
    height: float = 12.0


def _add_textbox_sync(pptx_path: str, req: "AddTextBoxReq") -> int:
    from pptx import Presentation
    from pptx.util import Emu, Pt

    prs = Presentation(pptx_path)
    if req.slide_index >= len(prs.slides):
        raise ValueError(f"Slide {req.slide_index} does not exist in the file")
    slide = prs.slides[req.slide_index]
    sw, sh = int(prs.slide_width), int(prs.slide_height)

    box = slide.shapes.add_textbox(
        Emu(int(sw * req.left / 100)), Emu(int(sh * req.top / 100)),
        Emu(int(sw * req.width / 100)), Emu(int(sh * req.height / 100)),
    )
    tf = box.text_frame
    tf.word_wrap = True
    tf.paragraphs[0].text = req.text
    run = tf.paragraphs[0].runs[0]
    run.font.size = Pt(24)
    prs.save(pptx_path)
    return box.shape_id


@router.post("/slide/shape-add-text")
async def add_text_box(req: AddTextBoxReq):
    """Insert a new text box onto the slide at the given percent geometry —
    the write path behind the canvas toolbar's "Add text box" button."""
    if re.search(r'[/\\.]\.', req.session_id):
        raise HTTPException(400, "Invalid session id")
    pptx_path = f"data/ppt/{req.session_id}.pptx"
    if not os.path.exists(pptx_path):
        raise HTTPException(404, "No presentation found for this session")

    try:
        new_shape_id = await asyncio.to_thread(_add_textbox_sync, pptx_path, req)
    except ValueError as e:
        raise HTTPException(400, str(e))

    slides = await refresh_slide_thumbnails_async(req.session_id)
    if slides is None:
        raise HTTPException(400, f"Slide index {req.slide_index} out of range")
    return {"status": "ok", "shape_id": new_shape_id, "slides": slides}


class ShapeDeleteReq(BaseModel):
    session_id:  str
    slide_index: int
    shape_id:    int


def _delete_shape_sync(pptx_path: str, req: "ShapeDeleteReq"):
    from pptx import Presentation
    from services.ppt_template_builder import _shape_by_id

    prs = Presentation(pptx_path)
    if req.slide_index >= len(prs.slides):
        raise ValueError(f"Slide {req.slide_index} does not exist in the file")
    slide = prs.slides[req.slide_index]
    shape = _shape_by_id(slide, req.shape_id)
    if shape is None:
        raise ValueError(f"Shape {req.shape_id} does not exist on slide {req.slide_index}")
    shape._element.getparent().remove(shape._element)
    prs.save(pptx_path)


@router.delete("/slide/shape")
async def delete_shape(req: ShapeDeleteReq):
    """Remove a shape from a slide entirely — the write path behind the
    canvas toolbar's "Delete" button."""
    if re.search(r'[/\\.]\.', req.session_id):
        raise HTTPException(400, "Invalid session id")
    pptx_path = f"data/ppt/{req.session_id}.pptx"
    if not os.path.exists(pptx_path):
        raise HTTPException(404, "No presentation found for this session")

    try:
        await asyncio.to_thread(_delete_shape_sync, pptx_path, req)
    except ValueError as e:
        raise HTTPException(400, str(e))

    slides = await refresh_slide_thumbnails_async(req.session_id)
    if slides is None:
        raise HTTPException(400, f"Slide index {req.slide_index} out of range")
    return {"status": "ok", "slides": slides}


class ShapeZOrderReq(BaseModel):
    session_id:  str
    slide_index: int
    shape_id:    int
    direction:   str   # "front" | "back"


def _reorder_shape_sync(pptx_path: str, req: "ShapeZOrderReq"):
    from pptx import Presentation
    from services.ppt_template_builder import _shape_by_id

    prs = Presentation(pptx_path)
    if req.slide_index >= len(prs.slides):
        raise ValueError(f"Slide {req.slide_index} does not exist in the file")
    slide = prs.slides[req.slide_index]
    shape = _shape_by_id(slide, req.shape_id)
    if shape is None:
        raise ValueError(f"Shape {req.shape_id} does not exist on slide {req.slide_index}")
    spTree = shape._element.getparent()
    el = shape._element
    spTree.remove(el)
    if req.direction == "front":
        spTree.append(el)
    else:
        # Insert after the last non-shape (grpSpPr/nvGrpSpPr) element so it
        # lands at the very back of the drawable shapes, not before them.
        idx = 0
        for i, child in enumerate(spTree):
            if child.tag.endswith("}nvGrpSpPr") or child.tag.endswith("}grpSpPr"):
                idx = i + 1
        spTree.insert(idx, el)
    prs.save(pptx_path)


@router.patch("/slide/shape-zorder")
async def reorder_shape(req: ShapeZOrderReq):
    """Bring a shape to front or send it to back — the write path behind the
    canvas toolbar's layering buttons."""
    if re.search(r'[/\\.]\.', req.session_id):
        raise HTTPException(400, "Invalid session id")
    if req.direction not in ("front", "back"):
        raise HTTPException(400, "direction must be 'front' or 'back'")
    pptx_path = f"data/ppt/{req.session_id}.pptx"
    if not os.path.exists(pptx_path):
        raise HTTPException(404, "No presentation found for this session")

    try:
        await asyncio.to_thread(_reorder_shape_sync, pptx_path, req)
    except ValueError as e:
        raise HTTPException(400, str(e))

    slides = await refresh_slide_thumbnails_async(req.session_id)
    if slides is None:
        raise HTTPException(400, f"Slide index {req.slide_index} out of range")
    return {"status": "ok", "slides": slides}


@router.patch("/slide/geometry")
async def edit_slide_geometry(req: SlideGeometryReq):
    """Move/resize/rotate shapes on a slide — the write path behind the WYSIWYG
    canvas. Accepts a batch of shape geometry deltas (percent), keyed by the
    stable shape_id, so a single drag-and-resize is one round-trip."""
    if re.search(r'[/\\.]\.', req.session_id):
        raise HTTPException(400, "Invalid session id")
    pptx_path = f"data/ppt/{req.session_id}.pptx"
    if not os.path.exists(pptx_path):
        raise HTTPException(404, "No presentation found for this session")
    if not req.shapes:
        raise HTTPException(400, "No shapes provided")

    slides = await apply_shape_geometry_async(req.session_id, req.slide_index, req.shapes)
    if slides is None:
        raise HTTPException(400, f"Slide index {req.slide_index} out of range")
    return {"status": "ok", "slides": slides}


async def add_slide_fast_async(session_id: str, kind: str, data: dict,
                                insert_after: Optional[int] = None) -> Optional[int]:
    """
    Inserts a new slide and updates the kinds sidecar — the fast part of
    adding a slide (no LibreOffice involved, just python-pptx XML writes),
    split out the same way apply_slide_edit_fast_async is so voice tools can
    reply as soon as the file write succeeds instead of waiting for a
    full-deck thumbnail re-render too. Call refresh_slide_thumbnails_async
    afterward for that. Returns the new slide's index, or None if the
    session has no presentation on disk.
    """
    pptx_path = f"data/ppt/{session_id}.pptx"
    if not os.path.exists(pptx_path):
        return None

    from services.ppt_template_builder import add_slide_to_deck

    def _current_count() -> int:
        from pptx import Presentation
        return len(Presentation(pptx_path).slides)

    count = await asyncio.to_thread(_current_count)
    kinds = _load_kinds(session_id) or []
    if len(kinds) != count:
        kinds = kinds[:count] + [None] * max(0, count - len(kinds))
    sources = _load_sources(session_id) or []
    if len(sources) != count:
        sources = sources[:count] + [None] * max(0, count - len(sources))

    # Clone an existing same-kind slide already in THIS deck when there is
    # one — avoids the cross-package image copy entirely for the common
    # case, and inherits that slide's exact source so the slot map still
    # matches. Otherwise a random candidate is pulled from the template
    # pool (see _KIND_SOURCES) — different each time, on purpose.
    source_index_in_deck = next((i for i, k in enumerate(kinds) if k == kind), None)
    existing_source = sources[source_index_in_deck] if source_index_in_deck is not None else None

    new_index, new_source = await asyncio.to_thread(
        add_slide_to_deck, pptx_path, kind, data, source_index_in_deck, existing_source, insert_after
    )

    kinds.insert(new_index, kind)
    sources.insert(new_index, new_source)
    _save_kinds(session_id, kinds)
    _save_sources(session_id, sources)
    return new_index


async def add_slide_async(session_id: str, kind: str, data: dict,
                           insert_after: Optional[int] = None) -> Optional[List[dict]]:
    """add_slide_fast_async + a synchronous thumbnail refresh — used by the
    HTTP endpoint below, whose caller needs the updated slides list back in
    the same response. Voice tools use add_slide_fast_async + a backgrounded
    refresh_slide_thumbnails_async instead — see tools/ppt_copilot.py."""
    new_index = await add_slide_fast_async(session_id, kind, data, insert_after)
    if new_index is None:
        return None
    return await refresh_slide_thumbnails_async(session_id)


class AddSlideReq(BaseModel):
    session_id:   str
    kind:         str
    data:         dict
    insert_after: Optional[int] = None  # None = append at the end


@router.post("/slide/add")
async def add_slide(req: AddSlideReq):
    """Insert a new slide into an already-generated/uploaded presentation.
    Unlike PATCH /slide[/kind] (which mutate one existing slide), this
    creates a new one — see add_slide_async / ppt_template_builder.add_slide_to_deck."""
    if re.search(r'[/\\.]\.', req.session_id):
        raise HTTPException(400, "Invalid session id")
    pptx_path = f"data/ppt/{req.session_id}.pptx"
    if not os.path.exists(pptx_path):
        raise HTTPException(404, "No presentation found for this session")

    from services.ppt_template_builder import KIND_FIELD_SCHEMA
    if req.kind not in KIND_FIELD_SCHEMA:
        raise HTTPException(400, f"Unknown slide kind: {req.kind!r}")

    slides = await add_slide_async(req.session_id, req.kind, req.data, req.insert_after)
    if slides is None:
        raise HTTPException(400, "Failed to add slide")

    return {"status": "ok", "slides": slides, "kinds": _load_kinds(req.session_id)}


class AddSlideFromInstructionReq(BaseModel):
    session_id:   str
    instruction:  str
    insert_after: Optional[int] = None


@router.post("/slide/add-generate")
async def add_slide_from_instruction(req: AddSlideFromInstructionReq):
    """UI counterpart to the ppt_add_slide voice tool — same one-field 'what
    should this slide be about' flow as Create PPT, instead of asking the
    user to pick a kind and fill a structured form by hand. Generates the
    kind + content from the instruction, falling back to a plain title-only
    text slide if generation fails or the instruction is empty."""
    if re.search(r'[/\\.]\.', req.session_id):
        raise HTTPException(400, "Invalid session id")
    pptx_path = f"data/ppt/{req.session_id}.pptx"
    if not os.path.exists(pptx_path):
        raise HTTPException(404, "No presentation found for this session")

    from services.ppt_template_builder import generate_single_slide_content

    instruction = req.instruction.strip()
    slide_data = None
    if instruction:
        try:
            slide_data = await generate_single_slide_content(instruction)
        except Exception as e:
            logger.error(f"add_slide_from_instruction generation error: {e}")

    if not slide_data:
        slide_data = {"kind": "text", "title": instruction[:60] or "New Slide", "paragraphs": []}

    kind = slide_data.pop("kind")
    slides = await add_slide_async(req.session_id, kind, slide_data, req.insert_after)
    if slides is None:
        raise HTTPException(400, "Failed to add slide")

    return {"status": "ok", "slides": slides, "kinds": _load_kinds(req.session_id),
            "kind": kind, "title": slide_data.get("title", "")}


def _patch_notes_batch_sync(pptx_path: str, notes_by_index: dict[int, str]) -> None:
    """Write speaker notes for multiple slides in a single Presentation open/save."""
    from pptx import Presentation

    prs = Presentation(pptx_path)
    for slide_index, notes in notes_by_index.items():
        if slide_index >= len(prs.slides):
            continue
        slide = prs.slides[slide_index]
        try:
            notes_slide = slide.notes_slide
            notes_tf    = notes_slide.notes_text_frame
            notes_tf.clear()
            para = notes_tf.paragraphs[0] if notes_tf.paragraphs else notes_tf.add_paragraph()
            run  = para.add_run()
            run.text = notes
        except Exception as e:
            logger.warning(f"Could not write speaker notes for slide {slide_index}: {e}")
    prs.save(pptx_path)


async def apply_notes_batch_async(session_id: str, notes_by_index: dict[int, str]) -> Optional[List[dict]]:
    """
    Writes speaker notes for multiple slides in one file open/save and one
    thumbnail re-render. apply_slide_edit_async re-renders the WHOLE deck via
    LibreOffice on every call, so looping it once per slide for a batch op
    ("generate notes for all slides") would mean N full-deck renders where
    one suffices.
    """
    pptx_path = f"data/ppt/{session_id}.pptx"
    if not os.path.exists(pptx_path):
        return None

    slides_mem = _slide_store.get(session_id, [])
    for slide_index, old_slide in enumerate(slides_mem):
        if slide_index in notes_by_index:
            _remember_slide_version(session_id, slide_index, old_slide)

    try:
        from db.engine import AsyncSessionLocal
        from db.models import PPTSlideVersion
        async with AsyncSessionLocal() as db_session:
            for slide_index, old_slide in enumerate(slides_mem):
                if slide_index not in notes_by_index:
                    continue
                v = PPTSlideVersion(
                    session_id=session_id,
                    slide_index=slide_index,
                    title=old_slide.get("title", ""),
                    bullets=json.dumps(_extract_slide_bullets(old_slide)),
                    notes=old_slide.get("notes", ""),
                )
                db_session.add(v)
            await db_session.commit()
    except Exception as e:
        logger.error(f"Failed to record slide versions (batch): {e}")

    await asyncio.to_thread(_patch_notes_batch_sync, pptx_path, notes_by_index)

    img_dir   = f"data/ppt/slides/{session_id}"
    img_paths = await asyncio.to_thread(_convert_to_images_sync, pptx_path, img_dir)

    slides = await _extract_slides(pptx_path)
    version = int(time.time() * 1000)
    for i, slide in enumerate(slides):
        if i < len(img_paths):
            fname = os.path.basename(img_paths[i])
            slide["image_url"] = f"/api/v1/ppt/image/{session_id}/{fname}?v={version}"

    _slide_store[session_id] = slides
    return slides


@router.get("/slide/{session_id}/{slide_index}/versions")
async def get_slide_versions(session_id: str, slide_index: int):
    """Retrieve all past versions of a slide, ordered newest to oldest."""
    from db.engine import AsyncSessionLocal
    from db.models import PPTSlideVersion
    from sqlalchemy import select

    try:
        async with AsyncSessionLocal() as db_session:
            stmt = (
                select(PPTSlideVersion)
                .where(PPTSlideVersion.session_id == session_id)
                .where(PPTSlideVersion.slide_index == slide_index)
                .order_by(PPTSlideVersion.created_at.desc())
            )
            result = await db_session.execute(stmt)
            versions = result.scalars().all()
            
            db_versions = [
                {
                    "id": v.id,
                    "title": v.title,
                    "bullets": json.loads(v.bullets) if v.bullets else [],
                    "notes": v.notes,
                    "created_at": v.created_at.isoformat() if v.created_at else None
                } for v in versions
            ]
            return {
                "status": "ok",
                "versions": db_versions or _slide_version_store.get(session_id, {}).get(slide_index, [])
            }
    except Exception as e:
        logger.error(f"Failed to retrieve slide versions: {e}")
        return {"status": "ok", "versions": _slide_version_store.get(session_id, {}).get(slide_index, [])}


_BULLET_LINE_RE = re.compile(r'^\d+\.\s{1,3}(.+)')


def _set_shape_text(shape, new_text: str, *, size_pt: Optional[float] = None, color: Optional[tuple] = None):
    """Replace a single shape's visible text with `new_text` in its first run,
    clearing any other runs/paragraphs. Only ever touches the one shape passed
    in — callers are responsible for picking the correct shape so unrelated
    shapes on the slide are never modified."""
    from pptx.util import Pt
    from pptx.dml.color import RGBColor

    tf = shape.text_frame
    for para in tf.paragraphs:
        for run in para.runs:
            run.text = ""
    if tf.paragraphs:
        first_para = tf.paragraphs[0]
        if first_para.runs:
            first_para.runs[0].text = new_text
            run = first_para.runs[0]
        else:
            from pptx.oxml.ns import qn
            from lxml import etree
            r_elem = etree.SubElement(first_para._p, qn('a:r'))
            etree.SubElement(r_elem, qn('a:rPr'), attrib={'lang': 'en-US'})
            t_elem = etree.SubElement(r_elem, qn('a:t'))
            t_elem.text = new_text
            run = None
        if run is not None:
            try:
                if size_pt is not None:
                    run.font.size = Pt(size_pt)
                if color is not None:
                    run.font.color.rgb = RGBColor(*color)
            except Exception:
                pass


def _find_bullet_body_shape(slide, title_shape):
    """Locate the exact shape that holds the numbered '1. ...' bullet text —
    i.e. the same shape _extract_slide_bullets() reads from. Returns None if
    no shape matches, rather than guessing, so callers never overwrite an
    unrelated text box (subtitle, footer, caption, etc.) by mistake.

    A shape only qualifies if EVERY non-empty paragraph in it matches the
    numbered-bullet pattern — not just one. Requiring only one matching
    paragraph (the previous behaviour) meant a shape with mixed content —
    e.g. a subtitle/name+date box where only one of several lines happened
    to look list-like — could be misidentified as "the bullet body" and
    have its entire text_frame wiped via tf.clear() during an edit that
    only meant to touch actual numbered bullets.

    NOTE: python-pptx re-wraps each shape in a fresh proxy object every time
    `slide.shapes` is iterated, so comparing across two separate loops with
    `is` (object identity) silently always evaluates False — even for the
    exact same underlying shape. Compare by `shape_id` (a stable int) instead.
    This was part of the original bug: the old code's "first shape that is
    not title_shape" check never actually excluded the title shape.
    """
    title_id = getattr(title_shape, "shape_id", None)
    for shape in slide.shapes:
        if not shape.has_text_frame or shape.shape_id == title_id:
            continue
        non_empty = [p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip()]
        if non_empty and all(_BULLET_LINE_RE.match(line) for line in non_empty):
            return shape
    return None


def _patch_slide(
    slide,
    slide_index: int,
    title: str,
    bullets: list[str],
    notes: str,
    other_edits: Optional[dict] = None,
):
    """
    Rewrite a single (already-open) slide's title, numbered-bullet body,
    speaker notes, and (optionally) any other individual shape's text using
    python-pptx. Opening/saving the file is the caller's job — see
    apply_slide_edit_async — so this can be shared with the kind-aware
    writer, which needs the same version/thumbnail/_slide_store plumbing
    around a different mutation.

    Each of title / bullets / notes / other_edits only ever touches the exact
    shape it corresponds to — nothing else on the slide is cleared or
    rewritten. This matters because a slide can contain extra shapes (a
    subtitle, a footer, a caption) that must survive an edit untouched, e.g.
    "change the title of slide 3" must not wipe unrelated text elsewhere on
    the slide.
    """
    # ── Identify title shape ──────────────────────────────────────────────────
    title_shape = None
    for shape in slide.shapes:
        if shape.has_text_frame and "title" in shape.name.lower():
            title_shape = shape
            break
    # Fallback: first shape with a large font (heuristic for untitled placeholders)
    if not title_shape:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        try:
                            if run.font.size and run.font.size.pt >= 24:
                                title_shape = shape
                                break
                        except Exception:
                            pass
                if title_shape:
                    break

    # ── Identify the bullet/body shape (only if it actually holds bullets) ───
    # Fixes the bug where "first non-title shape with a text frame" could pick
    # an unrelated text box and clobber it during a title-only edit.
    body_shape = _find_bullet_body_shape(slide, title_shape)

    logger.info(
        f"_patch_slide slide={slide_index} "
        f"title_shape={getattr(title_shape, 'shape_id', None)} "
        f"body_shape={getattr(body_shape, 'shape_id', None)} "
        f"bullets_provided={len(bullets) if bullets else 0} "
        f"other_edits_keys={list((other_edits or {}).keys())}"
    )

    if title_shape is not None and title is not None:
        _set_shape_text(title_shape, title)

    if body_shape is not None and bullets:
        from pptx.util import Pt
        from pptx.dml.color import RGBColor
        tf = body_shape.text_frame
        tf.clear()  # only ever the confirmed bullet shape, never a guess
        for i, bullet_text in enumerate(bullets):
            para = tf.add_paragraph() if i > 0 else tf.paragraphs[0]
            run = para.add_run()
            run.text = f"{i + 1}.  {bullet_text}"
            try:
                run.font.size = Pt(17)
                run.font.color.rgb = RGBColor(0xCC, 0xCC, 0xCC)
            except Exception:
                pass

    # ── Update any other individual shape by its stable shape_id ─────────────
    # Lets edits reach content beyond the title/bullets (a subtitle, callout,
    # caption, etc.) without risk of touching shapes that weren't named —
    # each entry addresses exactly one shape by id.
    if other_edits:
        handled_ids = {getattr(title_shape, "shape_id", None), getattr(body_shape, "shape_id", None)}
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            sid = getattr(shape, "shape_id", None)
            if sid in handled_ids or sid is None:
                continue
            key = str(sid)
            if key in other_edits:
                _set_shape_text(shape, other_edits[key])

    # ── Update speaker notes ──────────────────────────────────────────────────
    if notes is not None:
        try:
            notes_slide = slide.notes_slide
            notes_tf    = notes_slide.notes_text_frame
            notes_tf.clear()
            para = notes_tf.paragraphs[0] if notes_tf.paragraphs else notes_tf.add_paragraph()
            run  = para.add_run()
            run.text = notes
        except Exception as e:
            logger.warning(f"Could not write speaker notes for slide {slide_index}: {e}")

    logger.info(f"Patched slide {slide_index}")


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


async def _extract_slides(path: str) -> list[dict]:
    """Extract slide metadata (title, shapes, real speaker notes) from pptx."""
    try:
        import asyncio
        return await asyncio.to_thread(_extract_sync, path)
    except Exception as e:
        logger.error(f"_extract_slides error: {e}")
        return [{"index": i, "title": f"Slide {i+1}", "notes": ""} for i in range(10)]


def _extract_sync(path: str) -> list[dict]:
    from pptx import Presentation

    prs = Presentation(path)
    slides = []

    def _looks_like_decorative_text(text: str, width_pct: float, rotation: float) -> bool:
        t = text.strip()
        tl = t.lower()
        if not t:
            return True
        if re.search(r'\b(?:https?://|www\.|[\w.-]+\.(?:com|org|net|io|ai|co|in))\b', tl):
            return True
        if re.fullmatch(r'\d{1,2}(?:\s*/\s*\d{1,2})?', t):
            return True
        if re.fullmatch(r'\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}', t):
            return True
        if re.fullmatch(r'[A-Za-z]{3,9}\s+\d{4}', t):  # "July 2026" — month name + year, no day
            return True
        if abs(rotation or 0) > 1 or width_pct < 8:
            return True
        return False

    # Standard PPTX slide EMU dimensions
    SLIDE_W = int(prs.slide_width)  or 9144000
    SLIDE_H = int(prs.slide_height) or 6858000

    session_id = os.path.splitext(os.path.basename(path))[0]
    kinds = _load_kinds(session_id)
    sources = _load_sources(session_id)

    for i, slide in enumerate(prs.slides):
        kind = kinds[i] if kinds and i < len(kinds) else None
        source = sources[i] if sources and i < len(sources) else None

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

        # --- Shapes: text/images + styling + position ---
        title = ""
        body  = ""
        shapes_data: list[dict] = []
        title_candidates: list[tuple[float, str]] = []

        for shape in slide.shapes:
            try:
                s_left  = round((shape.left  or 0) / SLIDE_W * 100, 2)
                s_top   = round((shape.top   or 0) / SLIDE_H * 100, 2)
                s_width = round((shape.width or SLIDE_W) / SLIDE_W * 100, 2)
                s_height = round((shape.height or SLIDE_H) / SLIDE_H * 100, 2)
            except Exception:
                s_left, s_top, s_width, s_height = 0.0, 0.0, 90.0, 10.0
            try:
                s_rotation = float(shape.rotation or 0)
            except Exception:
                s_rotation = 0.0
            try:
                s_shape_id = shape.shape_id
            except Exception:
                s_shape_id = None

            is_picture = False
            try:
                from pptx.enum.shapes import MSO_SHAPE_TYPE
                is_picture = shape.shape_type == MSO_SHAPE_TYPE.PICTURE
            except Exception:
                is_picture = "picture" in str(getattr(shape, "shape_type", "")).lower()

            if is_picture:
                shapes_data.append({
                    "text":     "",
                    "color":    "#000000",
                    "size":     0,
                    "bold":     False,
                    "left":     s_left,
                    "top":      s_top,
                    "width":    s_width,
                    "height":   s_height,
                    "rotation": s_rotation,
                    "align":    "left",
                    "shape_id": s_shape_id,
                    "type":     "image",
                })
                continue

            if not shape.has_text_frame:
                continue
            text = shape.text_frame.text.strip()
            if not text:
                continue

            # None until a run reports an explicit RGB override — many shapes
            # inherit their color from the theme/layout, which python-pptx
            # cannot resolve to a concrete RGB, so we must not silently guess
            # one (previously defaulted to white, which lied to the toolbar's
            # color swatch on light-background decks).
            text_color: Optional[str] = None
            font_size  = 24.0
            is_bold    = False
            is_italic  = False
            is_underline = False
            font_name  = None
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
                        try:
                            if run.font.italic is not None:
                                is_italic = run.font.italic
                        except Exception:
                            pass
                        try:
                            if run.font.underline is not None:
                                is_underline = bool(run.font.underline)
                        except Exception:
                            pass
                        try:
                            if run.font.name is not None:
                                font_name = run.font.name
                        except Exception:
                            pass
                        break
                    break
            except Exception:
                pass

            shapes_data.append({
                "text":     text,
                "color":    text_color,
                "size":     min(float(font_size), 80.0),
                "bold":     is_bold,
                "italic":   is_italic,
                "underline": is_underline,
                "font":     font_name,
                "left":     s_left,
                "top":      s_top,
                "width":    s_width,
                "height":   s_height,      # emitted for the WYSIWYG canvas (was computed, never sent)
                "rotation": s_rotation,
                "align":    s_align,
                "shape_id": s_shape_id,
                "type":     "text",
            })

            # Title extraction is heuristic because uploaded decks often contain
            # brand marks, vertical URLs, dates, and slide numbers as ordinary
            # text boxes. Score plausible title text instead of taking the first
            # large shape in PPTX z-order.
            decorative = _looks_like_decorative_text(text, s_width, s_rotation)
            if not decorative:
                shape_name = shape.name.lower()
                is_placeholder_title = False
                try:
                    is_placeholder_title = "title" in str(shape.placeholder_format.type).lower()
                except Exception:
                    pass
                score = float(font_size)
                if "title" in shape_name or is_placeholder_title:
                    score += 80
                if is_bold:
                    score += 12
                if 8 <= s_top <= 72:
                    score += 8
                if s_height <= 25:
                    score += 4
                if len(text) <= 55:
                    score += 6
                # A short 2-line shape (e.g. "Presentation Title\nGrid Dynamics" —
                # a title stacked with a subtitle in one textbox, as GD template
                # cover slides do) is completely normal for a title shape to be.
                # Only penalize text that's genuinely body-shaped: more than 2
                # lines, or long overall.
                if text.count("\n") > 1 or len(text) > 90:
                    score -= 35
                title_candidates.append((score, text))
            body += text[:120] + " "

        if title_candidates:
            title = max(title_candidates, key=lambda item: item[0])[1]
        if not title and shapes_data:
            title = next(
                (s["text"] for s in shapes_data
                 if not _looks_like_decorative_text(s["text"], float(s.get("width") or 90), 0)),
                shapes_data[0]["text"],
            )
        if not title:
            title = f"Slide {i+1}"

        # ── Real speaker notes (from notes slide, not body text) ──────────────
        speaker_notes = ""
        try:
            if slide.has_notes_slide:
                notes_tf = slide.notes_slide.notes_text_frame
                # The first paragraph in a notes slide is often a placeholder title;
                # collect all non-empty paragraphs after index 0 for the real notes.
                note_parts = []
                for para in notes_tf.paragraphs:
                    t = para.text.strip()
                    if t:
                        note_parts.append(t)
                speaker_notes = "\n".join(note_parts)
        except Exception:
            pass

        # If this deck was generated from the GD template, its "kind" (e.g.
        # "team", "table") was persisted to a sidecar at generation time
        # (see _save_kinds) — a .pptx file has no such field of its own.
        # When known, use the kind-aware extractor's title instead of the
        # heuristic above: it's always correct (reads the exact shape_id the
        # generator wrote the title into), whereas the heuristic is a
        # best-effort guess needed only for uploaded/legacy decks that have
        # no kind. Uploaded decks have no sidecar, so `kind` stays None and
        # every kind-aware code path is skipped entirely for them.
        if kind:
            try:
                from services.ppt_template_builder import extract_slide_data, _parse_source
                kind_title = extract_slide_data(slide, kind, _parse_source(source)).get("title")
                if kind_title:
                    title = kind_title
            except Exception as e:
                logger.warning(f"kind-aware title extraction failed for slide {i} ({kind}): {e}")

        slides.append({
            "index":    i,
            "title":    title,
            "notes":    speaker_notes,
            "bg_color": bg_color,
            # Raised from 15 → 60 so the WYSIWYG canvas gets every editable
            # shape (a busy slide can exceed 15); the title heuristic above
            # already filters decoratively so extra shapes don't affect it.
            "shapes":   shapes_data[:60],
            "kind":     kind,
            "source":   source,
            # EMU slide dimensions so the frontend can build an aspect-correct
            # canvas and convert between its pixels and the shape percentages.
            "slide_width":  SLIDE_W,
            "slide_height": SLIDE_H,
        })

    return slides
