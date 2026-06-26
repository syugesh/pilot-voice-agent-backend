"""PPT API — navigate + file upload with slide extraction."""
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os, json, asyncio, subprocess, shutil, glob, logging, time

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
_latest_upload_sid: str = ""               # fallback key for voice-session lookups
_current_slide: dict[str, int] = {}        # session_id → 0-indexed current slide


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
