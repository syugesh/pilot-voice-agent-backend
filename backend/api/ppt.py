# ============================================================================
# DISABLED: PILOT-native api/ppt.py implementation (pre-GD-template).
# Superseded by the upstream GD-template implementation from
# syugesh/pilot-voice-agent-backend (feature/ppt-copilot @ 869dd08d9), pasted
# in active below. Kept here commented out for reference only — do not import.
# ============================================================================
# """
# Unified PowerPoint API — upload (instant, stream, prepare), high-fidelity rendering,
# navigation, Reveal.js viewer, and Slide Q&A.
# Consolidates both ppt.py and ppt_instant.py to eliminate duplication.
# """
# 
# 
# #                                       USER
# #                                         │
# #                      Upload PPT / Navigate / Ask Question
# #                                         │
# #                                         ▼
# #                         ┌────────────────────────────────┐
# #                         │      FastAPI PPT Router        │
# #                         │        (Unified ppt.py)        │
# #                         └────────────────────────────────┘
# #                                         │
# #           ┌─────────────────────────────┼─────────────────────────────┐
# #           │                             │                             │
# #           ▼                             ▼                             ▼
# #    Upload APIs                  Viewer APIs                  AI & Navigation APIs
# # ───────────────────      ───────────────────────      ───────────────────────────
# # /upload                 /viewer/{session_id}         /navigate
# # /upload_instant         /slides/{session_id}         /jump
# # /upload_stream                                     /summarise
# # /upload_prepare                                   /qa
# # /render_stream
# #           │
# #           ▼
# # ────────────────────────────────────────────────────────────────────────────
# #                     PowerPoint Processing Pipeline
# # ────────────────────────────────────────────────────────────────────────────
# #           │
# #           ▼
# # Read PPT File
# # (python-pptx)
# #           │
# #           ▼
# # Presentation Object
# #           │
# #           ▼
# # Loop Through Every Slide
# #           │
# #           ├─────────────────────────────────────┐
# #           │                                     │
# #           ▼                                     ▼
# # Extract Metadata                      Render Slide Image
# # (title, bullets, notes)                      │
# #                                              │
# #                             ┌────────────────┴────────────────┐
# #                             │                                 │
# #                             ▼                                 ▼
# #                    LibreOffice Available?                  No
# #                             │                                 │
# #                      Yes ───┘                                 │
# #                             ▼                                 ▼
# #                 PPT → PDF Conversion             SVG Renderer (_slide_to_png_b64)
# #                      (soffice)                             │
# #                             │                             │
# #                             ▼                             ▼
# #                     PyMuPDF Reads PDF              PyMuPDF Renders SVG
# #                             │                             │
# #                             └──────────────┬──────────────┘
# #                                            ▼
# #                                Base64 PNG Image
# #                                            │
# #                                            ▼
# #                              Slide Metadata + Image
# #                                            │
# #                                            ▼
# #                      Store in _slide_store (Memory Cache)
# #                                            │
# #           ┌────────────────────────────────┼──────────────────────────────────┐
# #           │                                │                                  │
# #           ▼                                ▼                                  ▼
# #  Reveal.js Viewer                  Navigation Engine                   AI Copilot
# #           │                                │                                  │
# #           ▼                                ▼                                  ▼
# #  Browser HTML                  Event Bus / Commands              Summary / Q&A
# # (next/prev/jump)                next, prev, goto                 using metadata
# 
# import warnings
# try:
#     from jwt.exceptions import InsecureKeyLengthWarning
#     warnings.filterwarnings("ignore", category=InsecureKeyLengthWarning)
# except ImportError:
#     pass
# 
# import asyncio
# import base64
# import html
# import io
# import json
# import logging
# import os
# import re
# import shutil
# import subprocess
# import tempfile
# import time
# from typing import List, Optional
# 
# from fastapi import APIRouter, File, HTTPException, UploadFile, Request
# from fastapi.responses import HTMLResponse, StreamingResponse
# from pydantic import BaseModel
# 
# from backend.core.slide_store import (
#     _current_slide,
#     _slide_store,
#     get_latest_upload_sid,
#     resolve_sid,
#     set_latest_upload_sid,
# )
# 
# router = APIRouter()
# logger = logging.getLogger("pilot.api.ppt")
# 
# 
# # ============================================================
# # MODELS
# # ============================================================
# 
# 
# class PPTCmd(BaseModel):
#     session_id: str
#     direction: str
#     slide_index: int = -1
# 
# 
# class JumpCmd(BaseModel):
#     session_id: str
#     query: str
# 
# 
# class CreateCmd(BaseModel):
#     session_id: str
#     prompt: str
#     slide_count: int = 5
# 
# 
# # ============================================================
# # RENDERERS & METADATA HELPERS
# # ============================================================
# 
# 
# 
# # ============================================================================
# # LEGACY PPT COPILOT IMPLEMENTATION — disabled 2026-07-10, superseded by the
# # GD-template implementation below. Route handlers below are commented out
# # since the new implementation registers routes at the same paths on the
# # same router; pure helper functions PILOT's own ppt_copilot.py tools still
# # depend on (create_presentation_from_prompt, save_and_render_pptx,
# # improvise_slide_content, and their transitive helpers) are kept active
# # below instead of being commented out.
# # ============================================================================
# 
# def _slide_to_png_b64(slide, prs_w: int, prs_h: int, width_px: int = 960, height_px: int = 540) -> str:
#     """Render one slide to a base64-encoded PNG string using PyMuPDF (no cairosvg/cairo dependency)."""
#     import fitz
# 
#     scale_x = width_px / prs_w
#     scale_y = height_px / prs_h
# 
#     # Background fill
#     bg_color = "#111112"
#     try:
#         bg = slide.background.fill
#         if hasattr(bg, "fore_color") and bg.fore_color.type:
#             rgb = bg.fore_color.rgb
#             bg_color = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
#     except Exception:
#         pass
# 
#     text_els = ""
#     for shape in slide.shapes:
#         if not shape.has_text_frame:
#             continue
#         x = int(shape.left * scale_x)
#         y = int(shape.top * scale_y)
# 
#         cursor_y = y + 22
#         for para in shape.text_frame.paragraphs:
#             raw = para.text.strip()
#             if not raw:
#                 cursor_y += 12
#                 continue
# 
#             font_size, bold, color = 18, False, "#ffffff"
#             try:
#                 if para.font.size:
#                     font_size = int(para.font.size.pt)
#                 if para.font.bold:
#                     bold = True
#                 if para.font.color and para.font.color.type:
#                     rgb = para.font.color.rgb
#                     color = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
#             except Exception:
#                 pass
#             # A paragraph may contain mixed formatting
#             for run in para.runs:
#                 try:
#                     if run.font.size:
#                         font_size = int(run.font.size.pt)
#                     if run.font.bold:
#                         bold = True
#                     if run.font.color and run.font.color.type:
#                         rgb = run.font.color.rgb
#                         color = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
#                 except Exception:
#                     pass
# 
#             fs = max(int(font_size * min(scale_x, scale_y) * 0.85), 8)
#             fw = "bold" if bold else "normal"
#             safe = html.escape(raw)
#             # Wrap long text with tspan elements
#             words = safe.split()
#             lines, cur_line, max_chars = [], [], int(width_px / (fs * 0.6))
#             for w in words:
#                 cur_line.append(w)
#                 if len(" ".join(cur_line)) > max_chars:
#                     lines.append(" ".join(cur_line[:-1]))
#                     cur_line = [w]
#             if cur_line:
#                 lines.append(" ".join(cur_line))
# 
#             for line in lines:
#                 text_els += (
#                     f'<text x="{x + 6}" y="{cursor_y}" '
#                     f'font-size="{fs}" font-weight="{fw}" fill="{color}" '
#                     f'font-family="Inter,Arial,sans-serif">'
#                     f"{line}</text>\n"
#                 )
#                 cursor_y += int(fs * 1.45)
#             cursor_y += 4
# 
#     svg = (
#         f'<svg xmlns="http://www.w3.org/2000/svg" '
#         f'width="{width_px}" height="{height_px}">'
#         f'<rect width="{width_px}" height="{height_px}" fill="{bg_color}"/>'
#         f"{text_els}"
#         f"</svg>"
#     )
# 
#     # Render SVG directly to PNG in memory using PyMuPDF (completely self-contained, no native cairo library needed)
#     try:
#         svg_doc = fitz.open("svg", svg.encode("utf-8"))
#         page = svg_doc[0]
#         pix = page.get_pixmap()
#         png_data = pix.tobytes("png")
#         svg_doc.close()
#         return base64.b64encode(png_data).decode()
#     except Exception as e:
#         logger.error(f"Fallback SVG-to-PNG render failed: {e}")
#         return ""
# 
# 
# def _is_footer_like_placeholder(shape) -> bool:
#     """True for placeholders that should never be treated as editable slide body text."""
#     if not getattr(shape, "is_placeholder", False):
#         return False
#     try:
#         from pptx.enum.shapes import PP_PLACEHOLDER
# 
#         return shape.placeholder_format.type in (
#             PP_PLACEHOLDER.DATE,
#             PP_PLACEHOLDER.FOOTER,
#             PP_PLACEHOLDER.SLIDE_NUMBER,
#         )
#     except Exception:
#         return False
# 
# 
# def _clear_corrupt_footer_placeholders(slide):
#     """Remove body text accidentally stored in slide number/footer/date placeholders."""
#     for shape in slide.shapes:
#         if not getattr(shape, "has_text_frame", False) or not _is_footer_like_placeholder(shape):
#             continue
#         text = shape.text_frame.text.strip()
#         if not text:
#             continue
#         # A real footer/slide-number placeholder is tiny. If it has sentence-like
#         # content, it came from a bad edit/import and will render as overflow.
#         has_sentence_text = len(text) > 20 or any(mark in text for mark in (".", ",", ":", ";"))
#         has_placeholder_marker = "<#>" in text or "‹#›" in text
#         if has_sentence_text or has_placeholder_marker:
#             shape.text_frame.clear()
# 
# 
# def _is_editable_content_shape(slide, shape) -> bool:
#     if not getattr(shape, "has_text_frame", False):
#         return False
#     if _is_footer_like_placeholder(shape):
#         return False
#     try:
#         if slide.shapes.title == shape:
#             return False
#     except Exception:
#         pass
# 
#     name_lower = getattr(shape, "name", "").lower()
#     text = shape.text_frame.text.strip()
#     if (
#         "title" in name_lower
#         or "footer" in name_lower
#         or "header" in name_lower
#         or shape.top < 0.45 * 914400
#         or shape.top >= 6.65 * 914400
#         or "Grid Dynamics" in text
#         or "PILOT Voice OS" in text
#         or text.startswith("Topic:")
#         or text.startswith("Slide ")
#         or text == "<#>"
#     ):
#         return False
#     return True
# 
# 
# def _clean_bullet_text(text: str) -> str:
#     return re.sub(r"^[\s•\-\*·\d\.\)]+", "", str(text or "")).strip()
# 
# 
# def _extract_bullets_from_text_frame(text_frame) -> list[str]:
#     bullets = []
#     for para in text_frame.paragraphs:
#         p_cleaned = _clean_bullet_text(para.text)
#         if (
#             p_cleaned
#             and p_cleaned != "<#>"
#             and not p_cleaned.isdigit()
#             and "Grid Dynamics" not in p_cleaned
#             and "PILOT Voice OS" not in p_cleaned
#             and not p_cleaned.startswith("Topic:")
#             and not p_cleaned.startswith("Slide ")
#         ):
#             bullets.append(p_cleaned)
#     return bullets
# 
# 
# def _get_primary_bullet_shape(slide):
#     from pptx.enum.shapes import PP_PLACEHOLDER
# 
#     pilot_shape = None
#     placeholder_shape = None
#     candidates = []
# 
#     for shape in slide.shapes:
#         if not _is_editable_content_shape(slide, shape):
#             continue
# 
#         if getattr(shape, "name", "") == "PilotEditorBullets":
#             pilot_shape = shape
#             continue
# 
#         placeholder_score = 0
#         if shape.is_placeholder:
#             try:
#                 if shape.placeholder_format.type in (PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT):
#                     placeholder_score = 1000
#                     placeholder_shape = shape
#             except Exception:
#                 pass
# 
#         text = shape.text_frame.text.strip()
#         bullet_score = 0
#         for para in shape.text_frame.paragraphs:
#             p_text = para.text.strip()
#             if p_text.startswith(("•", "-", "*")) or re.match(r"^\d+[\.\)]\s+", p_text):
#                 bullet_score += 80
#             elif p_text:
#                 bullet_score += 20
# 
#         area_score = int((shape.width or 0) * (shape.height or 0) / 1_000_000_000)
#         candidates.append((placeholder_score + bullet_score + area_score + min(len(text), 500), shape))
# 
#     if pilot_shape is not None and _extract_bullets_from_text_frame(pilot_shape.text_frame):
#         return pilot_shape
#     if placeholder_shape is not None:
#         return placeholder_shape
#     if candidates:
#         candidates.sort(key=lambda item: item[0], reverse=True)
#         return candidates[0][1]
#     return None
# 
# 
# def _extract_slide_meta(slide, index: int, prs_w: int, prs_h: int, session_id: str = "default") -> dict:
#     """Fast, highly-structured metadata extraction (title, bullets, notes) for one slide.
#     Filters out decorative elements, footer numbers, and topics to prevent duplicate compounding."""
#     title = ""
#     try:
#         if slide.shapes.title:
#             title = slide.shapes.title.text.strip()
#     except Exception:
#         pass
# 
#     # If title is still empty, look for a text shape at the top (top < 1.5 inches)
#     if not title:
#         for shape in slide.shapes:
#             if shape.has_text_frame:
#                 # pptx units are EMUs. 1 inch = 914400 EMUs.
#                 # 1.5 inches = 1371600 EMUs.
#                 if shape.top < 1371600:
#                     text = shape.text_frame.text.strip()
#                     if text and not any(k in text for k in ["Grid Dynamics", "PILOT Voice OS", "Topic:"]):
#                         title = text
#                         break
# 
#     bullets = []
#     bullet_shape = _get_primary_bullet_shape(slide)
#     if bullet_shape is not None:
#         bullets = [b for b in _extract_bullets_from_text_frame(bullet_shape.text_frame) if b != title]
# 
#     notes = ""
#     try:
#         if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
#             notes = slide.notes_slide.notes_text_frame.text.strip()[:300]
#     except Exception:
#         pass
# 
#     # Extract images from slide — save to disk AND return base64 data URIs for the frontend
#     slide_images = []
#     try:
#         os.makedirs("data/ppt/images", exist_ok=True)
#         for shape_idx, shape in enumerate(slide.shapes):
#             try:
#                 if shape.shape_type == 13 or shape.__class__.__name__ == "Picture" or hasattr(shape, "image"):
#                     image_bytes = shape.image.blob
#                     content_type = shape.image.content_type or "image/png"
#                     ext = shape.image.ext or "png"
#                     # Save to disk for potential reuse
#                     img_filename = f"{session_id}_slide{index}_img{shape_idx}.{ext}"
#                     img_path = os.path.join("data/ppt/images", img_filename)
#                     with open(img_path, "wb") as f_img:
#                         f_img.write(image_bytes)
#                     # Also return as base64 for direct frontend rendering
#                     b64 = base64.b64encode(image_bytes).decode()
#                     slide_images.append({
#                         "data_url": f"data:{content_type};base64,{b64}",
#                         "path": img_path,
#                         "left_in": round(shape.left / 914400.0, 2),
#                         "top_in": round(shape.top / 914400.0, 2),
#                         "width_in": round(shape.width / 914400.0, 2),
#                         "height_in": round(shape.height / 914400.0, 2),
#                     })
#             except Exception as img_err:
#                 logger.warning(f"Failed to extract image from shape {shape_idx}: {img_err}")
#     except Exception as e:
#         logger.error(f"Error scanning slide shapes for images: {e}")
# 
#     # Extract font styling details from slide elements
#     font_name = "Arial"
#     title_bold = True
#     title_italic = False
#     bullet_bold = False
#     bullet_italic = False
# 
#     # Check title shape font properties
#     try:
#         if slide.shapes.title and slide.shapes.title.has_text_frame:
#             p = slide.shapes.title.text_frame.paragraphs[0]
#             if p.font.name:
#                 font_name = p.font.name
#             if p.font.bold is not None:
#                 title_bold = p.font.bold
#             if p.font.italic is not None:
#                 title_italic = p.font.italic
#     except Exception:
#         pass
# 
#     # Check first bullet point font properties
#     try:
#         for shape in slide.shapes:
#             if shape.has_text_frame:
#                 if _is_footer_like_placeholder(shape):
#                     continue
#                 try:
#                     if slide.shapes.title == shape:
#                         continue
#                 except Exception:
#                     pass
#                 # Check footer/topic/header text
#                 t_check = shape.text_frame.text.strip()
#                 if not t_check or any(k in t_check for k in ["Grid Dynamics", "PILOT Voice OS", "Topic:"]):
#                     continue
#                 p = shape.text_frame.paragraphs[0]
#                 if p.font.bold is not None:
#                     bullet_bold = p.font.bold
#                 if p.font.italic is not None:
#                     bullet_italic = p.font.italic
#                 break
#     except Exception:
#         pass
# 
#     return {
#         "index": index,
#         "title": title or f"Slide {index + 1}",
#         "bullets": bullets[:10],
#         "notes": notes,
#         "images": slide_images,
#         "font_name": font_name,
#         "title_bold": title_bold,
#         "title_italic": title_italic,
#         "bullet_bold": bullet_bold,
#         "bullet_italic": bullet_italic,
#     }
# 
# 
# def _get_libreoffice_path() -> str | None:
#     """Resolves headless soffice executable binary path across platforms."""
#     soffice_path = shutil.which("soffice") or shutil.which("libreoffice")
#     if not soffice_path:
#         for p in [
#             "/Applications/LibreOffice.app/Contents/MacOS/soffice",
#             "/opt/homebrew/bin/soffice",
#             "/usr/local/bin/soffice",
#         ]:
#             if os.path.exists(p):
#                 soffice_path = p
#                 break
#     return soffice_path
# 
# 
# def _run_libreoffice_headless(
#     soffice_path: str,
#     input_file: str,
#     outdir: str,
#     timeout: float = 15.0,
# ) -> None:
#     """
#     Run LibreOffice in fully headless mode without triggering the macOS GUI,
#     dock bounce, or window server connection.
# 
#     Key flags used:
#       --headless            – no UI rendering
#       --norestore           – skip crash-recovery dialog on startup
#       --nofirststartwizard  – skip the first-run setup wizard
#       -env:UserInstallation – isolated per-run profile so LO does not
#                               communicate with an already-running GUI instance
#     The subprocess also inherits a sanitised environment with DISPLAY unset
#     so the VCL backend cannot attach to any X11 / Aqua window server.
#     """
#     profile_dir = os.path.join(outdir, "lo_profile")
#     os.makedirs(profile_dir, exist_ok=True)
#     abs_profile = os.path.abspath(profile_dir)
#     # Build a proper file:/// URI (works on both macOS and Linux)
#     profile_url = "file:///" + abs_profile.lstrip("/")
# 
#     cmd = [
#         soffice_path,
#         "--headless",
#         "--norestore",
#         "--nofirststartwizard",
#         f"-env:UserInstallation={profile_url}",
#         "--convert-to", "pdf",
#         "--outdir", outdir,
#         os.path.abspath(input_file),
#     ]
# 
#     # Strip DISPLAY so the VCL layer cannot open a window server connection
#     env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "DBUS_SESSION_BUS_ADDRESS")}
# 
#     subprocess.run(
#         cmd,
#         check=True,
#         timeout=timeout,
#         stdout=subprocess.DEVNULL,
#         stderr=subprocess.DEVNULL,
#         env=env,
#     )
# 
# 
# def _build_reveal_html(slides: list, session_id: str) -> str:
#     """Generates the full HTML markup with embedded slides for Reveal.js."""
#     sections = ""
#     for i, s in enumerate(slides):
#         img_src = f"data:image/png;base64,{s['img_b64']}" if s.get("img_b64") else ""
#         sections += f"""
#         <section id="slide-{i}" style="text-align:center;">
#             <img src="{img_src}" style="max-width:100%; max-height:100%; object-fit:contain; border:none; box-shadow:none; background:transparent;" />
#         </section>
#         """
# 
#     return f"""
# <!DOCTYPE html>
# <html>
# <head>
# <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/reveal.js/5.1.0/reveal.min.css">
# <style>
# html, body {{
#     margin:0;
#     width:100%;
#     height:100%;
#     background:#111;
# }}
# .reveal {{
#     width:100%;
#     height:100%;
# }}
# </style>
# </head>
# <body>
# <div class="reveal">
#     <div class="slides">
#         {sections}
#     </div>
# </div>
# <script src="https://cdnjs.cloudflare.com/ajax/libs/reveal.js/5.1.0/reveal.min.js"></script>
# <script>
# Reveal.initialize({{
#     controls:true,
#     progress:true,
#     slideNumber:true,
#     hash:false
# }});
# 
# window.addEventListener("message", (e) => {{
#     const action = e.data?.action;
#     if(action === "next")
#         Reveal.next();
#     else if(action === "prev")
#         Reveal.prev();
#     else if(action === "first")
#         Reveal.slide(0);
#     else if(action === "last")
#         Reveal.slide({len(slides) - 1});
#     else if(action === "goto" && e.data.index !== undefined)
#         Reveal.slide(e.data.index);
# }});
# 
# Reveal.on("slidechanged", (event) => {{
#     parent.postMessage({{
#         type:"slide_changed",
#         index:event.indexh
#     }}, "*");
# }});
# </script>
# </body>
# </html>
# """
# 
# 
# # ============================================================
# # ENDPOINTS
# # ============================================================
# 
# 
# # ---- route handlers superseded by the new implementation below (kept for reference) ----
# # # ── Route A: Original upload (renders everything at once via PyMuPDF) ──
# # 
# # 
# # @router.post("/upload")
# # async def upload_ppt(session_id: str, file: UploadFile = File(...)):
# #     """Upload PPTX → returns simple status. Renders all slides synchronously via PyMuPDF."""
# #     if not file.filename.lower().endswith((".pptx", ".ppt")):
# #         raise HTTPException(status_code=400, detail="Only PPT/PPTX supported")
# # 
# #     content = await file.read()
# #     # Save original presentation to disk
# #     os.makedirs("data/ppt", exist_ok=True)
# #     pptx_path = f"data/ppt/{session_id}.pptx"
# #     with open(pptx_path, "wb") as f_out:
# #         f_out.write(content)
# # 
# #     def _render():
# #         from pptx import Presentation
# # 
# #         prs = Presentation(io.BytesIO(content))
# #         slides = []
# #         for i, slide in enumerate(prs.slides):
# #             meta = _extract_slide_meta(slide, i, prs.slide_width, prs.slide_height, session_id=session_id)
# #             meta["img_b64"] = _slide_to_png_b64(slide, prs.slide_width, prs.slide_height)
# #             slides.append(meta)
# #         return slides
# # 
# #     slides = await asyncio.to_thread(_render)
# #     _slide_store[session_id] = slides
# #     set_latest_upload_sid(session_id)
# #     _current_slide[session_id] = 0
# # 
# #     return {"status": "ok", "slide_count": len(slides)}
# # 
# # 
# # # ── Route B: upload_instant (PDF-rendered slide flow with PyMuPDF fallback) ──
# # 
# # 
# # @router.post("/upload_instant")
# # async def upload_instant(session_id: str, file: UploadFile = File(...)):
# #     """Upload PPTX → returns JSON with all slides including high-fidelity images."""
# #     if not file.filename.lower().endswith((".pptx", ".ppt")):
# #         raise HTTPException(400, "Only .pptx / .ppt files supported")
# # 
# #     content = await file.read()
# #     # Save original presentation to disk
# #     os.makedirs("data/ppt", exist_ok=True)
# #     pptx_path = f"data/ppt/{session_id}.pptx"
# #     with open(pptx_path, "wb") as f_out:
# #         f_out.write(content)
# # 
# #     def _render_all() -> list[dict]:
# #         import fitz
# #         from pptx import Presentation
# # 
# #         try:
# #             if hasattr(fitz, "TOOLS"):
# #                 fitz.TOOLS.mupdf_display_errors(False)
# #         except Exception:
# #             pass
# # 
# #         prs = Presentation(io.BytesIO(content))
# #         prs_w = prs.slide_width
# #         prs_h = prs.slide_height
# # 
# #         slides_out = []
# #         for i, slide in enumerate(prs.slides):
# #             meta = _extract_slide_meta(slide, i, prs_w, prs_h, session_id=session_id)
# #             slides_out.append(meta)
# # 
# #         soffice_path = _get_libreoffice_path()
# #         pdf_rendered = False
# # 
# #         if soffice_path:
# #             try:
# #                 with tempfile.TemporaryDirectory() as temp_dir:
# #                     pptx_temp_path = os.path.join(temp_dir, "presentation.pptx")
# #                     with open(pptx_temp_path, "wb") as f_temp:
# #                         f_temp.write(content)
# # 
# #                     _run_libreoffice_headless(soffice_path, pptx_temp_path, temp_dir)
# # 
# #                     pdf_path = os.path.join(temp_dir, "presentation.pdf")
# #                     if os.path.exists(pdf_path):
# #                         doc = fitz.open(pdf_path)
# #                         for i, page in enumerate(doc):
# #                             if i < len(slides_out):
# #                                 pix = page.get_pixmap(dpi=120)
# #                                 png_bytes = pix.tobytes("png")
# #                                 slides_out[i]["img_b64"] = base64.b64encode(png_bytes).decode()
# #                         doc.close()
# #                         pdf_rendered = True
# #                         logger.info("Successfully rendered all slides to PNG via LibreOffice PDF pipeline.")
# #             except Exception as lo_err:
# #                 logger.error(f"LibreOffice instant conversion failed: {lo_err}")
# # 
# #         if not pdf_rendered:
# #             for i, slide in enumerate(prs.slides):
# #                 slides_out[i]["img_b64"] = _slide_to_png_b64(slide, prs_w, prs_h)
# # 
# #         return slides_out
# # 
# #     slides = await asyncio.to_thread(_render_all)
# #     _slide_store[session_id] = slides
# # 
# #     return {"status": "ok", "slide_count": len(slides), "slides": slides}
# # 
# # 
# # # ── Route C: upload_stream (streams PNG slides slide-by-slide via SSE) ──
# # 
# # 
# # @router.post("/upload_stream")
# # async def upload_stream(session_id: str, file: UploadFile = File(...)):
# #     """Upload PPTX → SSE stream. Emits rendered PNG slides slide-by-slide."""
# #     if not file.filename.lower().endswith((".pptx", ".ppt")):
# #         raise HTTPException(400, "Only .pptx / .ppt files supported")
# # 
# #     content = await file.read()
# #     # Save original presentation to disk
# #     os.makedirs("data/ppt", exist_ok=True)
# #     pptx_path = f"data/ppt/{session_id}.pptx"
# #     with open(pptx_path, "wb") as f_out:
# #         f_out.write(content)
# # 
# #     async def _event_generator():
# #         import fitz
# #         from pptx import Presentation
# # 
# #         prs = Presentation(io.BytesIO(content))
# #         prs_w = prs.slide_width
# #         prs_h = prs.slide_height
# #         total = len(prs.slides)
# #         store_list: list[dict] = []
# # 
# #         yield f"data: {json.dumps({'type': 'init', 'total': total})}\n\n"
# # 
# #         soffice_path = _get_libreoffice_path()
# #         pdf_doc = None
# # 
# #         if soffice_path:
# #             try:
# #                 temp_dir = tempfile.mkdtemp()
# #                 pptx_temp_path = os.path.join(temp_dir, "presentation.pptx")
# #                 with open(pptx_temp_path, "wb") as f_temp:
# #                     f_temp.write(content)
# # 
# #                 _run_libreoffice_headless(soffice_path, pptx_temp_path, temp_dir, timeout=10.0)
# # 
# #                 pdf_path = os.path.join(temp_dir, "presentation.pdf")
# #                 if os.path.exists(pdf_path):
# #                     pdf_doc = fitz.open(pdf_path)
# #             except Exception as lo_err:
# #                 logger.error(f"SSE background LibreOffice PDF conversion failed: {lo_err}")
# # 
# #         for i, slide in enumerate(prs.slides):
# #             meta = await asyncio.to_thread(_extract_slide_meta, slide, i, prs_w, prs_h, session_id=session_id)
# #             img_b64 = ""
# # 
# #             if pdf_doc and i < len(pdf_doc):
# #                 try:
# #                     page = pdf_doc[i]
# #                     pix = await asyncio.to_thread(page.get_pixmap, dpi=120)
# #                     png_bytes = pix.tobytes("png")
# #                     img_b64 = base64.b64encode(png_bytes).decode()
# #                 except Exception as page_err:
# #                     logger.error(f"Failed to render slide page {i} from PDF: {page_err}")
# # 
# #             if not img_b64:
# #                 img_b64 = await asyncio.to_thread(_slide_to_png_b64, slide, prs_w, prs_h)
# # 
# #             store_list.append({**meta, "img_b64": img_b64})
# #             payload = {**meta, "img_b64": img_b64, "total": total, "type": "slide"}
# #             yield f"data: {json.dumps(payload)}\n\n"
# # 
# #         if pdf_doc:
# #             pdf_doc.close()
# #             try:
# #                 shutil.rmtree(temp_dir, ignore_errors=True)
# #             except Exception:
# #                 pass
# # 
# #         _slide_store[session_id] = store_list
# #         set_latest_upload_sid(session_id)
# #         _current_slide[session_id] = 0
# # 
# #         yield f"data: {json.dumps({'type': 'done', 'total': total})}\n\n"
# # 
# #     return StreamingResponse(
# #         _event_generator(),
# #         media_type="text/event-stream",
# #         headers={
# #             "Cache-Control": "no-cache",
# #             "X-Accel-Buffering": "no",
# #             "Access-Control-Allow-Origin": "*",
# #         },
# #     )
# # 
# # 
# # # ── Route D: Prepared stream (upload_prepare + render_stream split) ──
# # 
# # 
# # @router.post("/upload_prepare")
# # async def upload_prepare(session_id: str, file: UploadFile = File(...)):
# #     """Fast file saver to prepare the presentation file on disk."""
# #     if not file.filename.lower().endswith((".pptx", ".ppt")):
# #         raise HTTPException(400, "Only .pptx / .ppt files supported")
# # 
# #     content = await file.read()
# #     os.makedirs("data/ppt", exist_ok=True)
# # 
# #     # Clear the images directory so stale extracted images from prior uploads are removed
# #     images_dir = "data/ppt/images"
# #     if os.path.exists(images_dir):
# #         import shutil as _shutil
# #         _shutil.rmtree(images_dir)
# #     os.makedirs(images_dir, exist_ok=True)
# # 
# #     path = f"data/ppt/{session_id}.pptx"
# #     with open(path, "wb") as f:
# #         f.write(content)
# #     return {"status": "ok"}
# # 
# # 
# # @router.get("/render_stream")
# # async def render_stream(session_id: str, token: str):
# #     """EventSource endpoint called by the frontend to render the slides page-by-page."""
# #     from backend.core.security import decode_token
# # 
# #     try:
# #         decode_token(token)
# #     except Exception:
# #         raise HTTPException(status_code=401, detail="Missing or invalid credentials")
# # 
# #     path = f"data/ppt/{session_id}.pptx"
# #     if not os.path.exists(path):
# #         raise HTTPException(404, "Prepared presentation file not found")
# # 
# #     async def _event_generator():
# #         import fitz
# #         from pptx import Presentation
# # 
# #         try:
# #             if hasattr(fitz, "TOOLS"):
# #                 fitz.TOOLS.mupdf_display_errors(False)
# #         except Exception:
# #             pass
# # 
# #         prs = Presentation(path)
# #         prs_w = prs.slide_width
# #         prs_h = prs.slide_height
# #         total = len(prs.slides)
# #         store_list: list[dict] = []
# # 
# #         yield f"data: {json.dumps({'type': 'init', 'total': total})}\n\n"
# # 
# #         soffice_path = _get_libreoffice_path()
# #         pdf_doc = None
# #         temp_dir = None
# # 
# #         if soffice_path:
# #             try:
# #                 temp_dir = tempfile.mkdtemp()
# #                 # Run in thread so the async event loop / SSE connection is not blocked
# #                 await asyncio.to_thread(
# #                     _run_libreoffice_headless, soffice_path, path, temp_dir, 15.0
# #                 )
# # 
# #                 pdf_filename = os.path.splitext(os.path.basename(path))[0] + ".pdf"
# #                 pdf_path = os.path.join(temp_dir, pdf_filename)
# #                 if os.path.exists(pdf_path):
# #                     pdf_doc = fitz.open(pdf_path)
# #             except Exception as lo_err:
# #                 logger.error(f"render_stream LibreOffice PDF conversion failed: {lo_err}")
# # 
# #         for i, slide in enumerate(prs.slides):
# #             meta = await asyncio.to_thread(_extract_slide_meta, slide, i, prs_w, prs_h, session_id=session_id)
# #             img_b64 = ""
# # 
# #             if pdf_doc and i < len(pdf_doc):
# #                 try:
# #                     page = pdf_doc[i]
# #                     pix = await asyncio.to_thread(page.get_pixmap, dpi=120)
# #                     png_bytes = pix.tobytes("png")
# #                     img_b64 = base64.b64encode(png_bytes).decode()
# #                 except Exception as page_err:
# #                     logger.error(f"Failed to render slide page {i} from PDF: {page_err}")
# # 
# #             if not img_b64:
# #                 img_b64 = await asyncio.to_thread(_slide_to_png_b64, slide, prs_w, prs_h)
# # 
# #             store_list.append({**meta, "img_b64": img_b64})
# #             payload = {**meta, "img_b64": img_b64, "total": total, "type": "slide"}
# #             yield f"data: {json.dumps(payload)}\n\n"
# # 
# #         if pdf_doc:
# #             pdf_doc.close()
# #         if temp_dir:
# #             try:
# #                 shutil.rmtree(temp_dir, ignore_errors=True)
# #             except Exception:
# #                 pass
# # 
# #         _slide_store[session_id] = store_list
# #         set_latest_upload_sid(session_id)
# #         _current_slide[session_id] = 0
# # 
# #         yield f"data: {json.dumps({'type': 'done', 'total': total})}\n\n"
# # 
# #     return StreamingResponse(
# #         _event_generator(),
# #         media_type="text/event-stream",
# #         headers={
# #             "Cache-Control": "no-cache",
# #             "X-Accel-Buffering": "no",
# #             "Access-Control-Allow-Origin": "*",
# #         },
# #     )
# # 
# # 
# # # ── Route E: Viewer & Slide Control ──
# # 
# # 
# # @router.get("/viewer/{session_id}", response_class=HTMLResponse)
# # async def get_viewer(session_id: str):
# #     """Returns a full, independent Reveal.js HTML slide presentation page."""
# #     slides = _slide_store.get(session_id)
# #     if not slides:
# #         return HTMLResponse("<h2>No slides uploaded.</h2>")
# #     return HTMLResponse(_build_reveal_html(slides, session_id))
# # 
# # 
# # @router.post("/navigate")
# # async def navigate(cmd: PPTCmd):
# #     """Executes a slide navigation action (next, prev, first, last) on the active session."""
# #     from backend.tools.ppt_copilot import ppt_navigate
# # 
# #     return await ppt_navigate({"direction": cmd.direction}, cmd.session_id)
# # 
# # 
# # @router.post("/jump")
# # async def jump(cmd: JumpCmd):
# #     """Analyzes natural language queries and navigates to matching slide index numbers."""
# #     from backend.queues.bus import bus
# # 
# #     slides = _slide_store .get(cmd.session_id, [])
# #     q = cmd.query.lower()
# #     m = re.search(r"\b(\d+)\b", q)
# #     if m:
# #         idx = int(m.group(1)) - 1
# #         await bus.emit_event("ppt_command", {"action": "goto", "index": idx}, cmd.session_id)
# #         return {"status": "ok", "index": idx}
# #     return {"status": "ok", "index": 0}
# # 
# # 
# # # ── Route F: Slide Summary & Q&A ──
# # 
# # 
# # @router.post("/summarise")
# # async def summarise(data: dict):
# #     """Compiles the first 20 slide titles to provide a fast contextual synopsis of the slide deck."""
# #     slides = _slide_store.get(data.get("session_id", ""), [])
# #     titles = ", ".join(slide["title"] for slide in slides[:20])
# #     return {"reply": f"Presentation contains {len(slides)} slides. Topics: {titles}"}
# # 
# # 
# # @router.get("/slides/{session_id}")
# # async def get_slides(session_id: str):
# #     """Retrieves list of slide metadata and images for the frontend viewer state."""
# #     slides = _slide_store.get(session_id) or _slide_store.get(get_latest_upload_sid(), [])
# #     return {"slides": slides}
# # 
# # 
# # @router.post("/qa")
# # async def qa(data: dict):
# #     """Asks questions about the slide deck, invoking the local PDF/text shape parser."""
# #     from backend.tools.ppt_copilot import ppt_qa
# # 
# #     return await ppt_qa({"query": data.get("query", "")}, data.get("session_id", ""))
# # 
# # 
# 
# async def download_ai_image(prompt_str: str) -> str | None:
#     """Helper to generate and download a professional AI image via Pollinations AI (Flux)
#     with fallbacks to Hugging Face serverless APIs (SDXL-Lightning, FLUX.1 Schnell) and stock photos."""
#     import asyncio
#     import random
#     import urllib.parse
#     import httpx
#     from backend.core.config import settings
# 
#     try:
#         os.makedirs("data/ppt/images", exist_ok=True)
#         filename = f"ai_gen_{int(time.time())}_{hash(prompt_str) % 10000}.png"
#         save_path = os.path.join("data/ppt/images", filename)
# 
#         # ── Priority 1: Pollinations AI (Flux Model) ──
#         logger.info(f"Priority 1: Attempting Pollinations AI for prompt: '{prompt_str}'")
#         encoded_prompt = urllib.parse.quote(prompt_str)
#         urls = [
#             f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=800&height=600&model=flux&nologo=true",
#             f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=800&height=600&nologo=true",
#         ]
# 
#         async with httpx.AsyncClient(timeout=30.0) as client:
#             for attempt in range(2):
#                 url = urls[attempt]
#                 try:
#                     # Apply sleep with jitter to stagger parallel downloads
#                     sleep_time = (attempt * 1.5) + random.uniform(0.5, 1.5)
#                     await asyncio.sleep(sleep_time)
# 
#                     resp = await client.get(url)
#                     if resp.status_code == 200:
#                         with open(save_path, "wb") as f:
#                             f.write(resp.content)
#                         logger.info(f"Downloaded generated AI image to: {save_path} on attempt {attempt + 1}")
#                         return save_path
#                     elif resp.status_code == 429:
#                         logger.warning(f"Pollinations AI rate limited (429). Retrying...")
#                     else:
#                         logger.warning(f"Pollinations AI returned status {resp.status_code}")
#                 except Exception as attempt_err:
#                     logger.error(f"Pollinations download attempt {attempt + 1} failed: {attempt_err}")
# 
#         # ── Fallback 2: SDXL-Lightning via Hugging Face Serverless API ──
#         if settings.HF_TOKEN:
#             logger.info(f"Fallback 2: Attempting SDXL-Lightning via Hugging Face API for prompt: '{prompt_str}'")
#             try:
#                 hf_url = "https://api-inference.huggingface.co/models/ByteDance/SDXL-Lightning"
#                 hf_headers = {"Authorization": f"Bearer {settings.HF_TOKEN}"}
#                 async with httpx.AsyncClient(timeout=30.0) as client:
#                     resp = await client.post(hf_url, headers=hf_headers, json={"inputs": prompt_str})
#                     if resp.status_code == 200:
#                         with open(save_path, "wb") as f:
#                             f.write(resp.content)
#                         logger.info(f"Downloaded generated AI image to: {save_path} using SDXL-Lightning")
#                         return save_path
#                     else:
#                         logger.warning(f"HF SDXL-Lightning returned status {resp.status_code}: {resp.text[:100]}")
#             except Exception as sdxl_err:
#                 logger.error(f"HF SDXL-Lightning call failed: {sdxl_err}")
# 
#         # ── Fallback 3: FLUX.1 Schnell via Hugging Face Serverless API ──
#         if settings.HF_TOKEN:
#             logger.info(f"Fallback 3: Attempting FLUX.1 Schnell via Hugging Face API for prompt: '{prompt_str}'")
#             try:
#                 hf_url = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
#                 hf_headers = {"Authorization": f"Bearer {settings.HF_TOKEN}"}
#                 async with httpx.AsyncClient(timeout=30.0) as client:
#                     resp = await client.post(hf_url, headers=hf_headers, json={"inputs": prompt_str})
#                     if resp.status_code == 200:
#                         with open(save_path, "wb") as f:
#                             f.write(resp.content)
#                         logger.info(f"Downloaded generated AI image to: {save_path} using FLUX.1 Schnell")
#                         return save_path
#                     else:
#                         logger.warning(f"HF FLUX.1 Schnell returned status {resp.status_code}: {resp.text[:100]}")
#             except Exception as flux_err:
#                 logger.error(f"HF FLUX.1 Schnell call failed: {flux_err}")
# 
#         # ── Fallback 4: High-quality neutral professional placeholder image from Unsplash ──
#         logger.info(f"Fallback 4: Attempting Unsplash placeholder image download for prompt: '{prompt_str}'")
#         try:
#             fallback_url = (
#                 "https://images.unsplash.com/photo-1557804506-669a67965ba0?auto=format&fit=crop&w=800&q=80"
#             )
#             async with httpx.AsyncClient(timeout=10.0) as client:
#                 resp = await client.get(fallback_url)
#                 if resp.status_code == 200:
#                     with open(save_path, "wb") as f:
#                         f.write(resp.content)
#                     logger.info(f"Rate limited on all AI generators. Used high-quality fallback image for prompt: '{prompt_str}'")
#                     return save_path
#         except Exception as fb_err:
#             logger.error(f"Ultimate fallback image download failed: {fb_err}")
# 
#     except Exception as e:
#         logger.error(f"Failed to generate and download AI image: {e}")
#     return None
# 
# 
# def clear_ppt_directory():
#     """Removes all files in data/ppt and data/ppt/images to start fresh."""
#     import shutil
# 
#     try:
#         ppt_dir = "data/ppt"
#         if os.path.exists(ppt_dir):
#             for filename in os.listdir(ppt_dir):
#                 file_path = os.path.join(ppt_dir, filename)
#                 try:
#                     if os.path.isdir(file_path):
#                         shutil.rmtree(file_path)
#                     else:
#                         os.unlink(file_path)
#                 except Exception as e:
#                     logger.warning(f"Failed to delete {file_path}: {e}")
#         os.makedirs(os.path.join(ppt_dir, "images"), exist_ok=True)
#         logger.info("Successfully cleaned and initialized data/ppt directory.")
#     except Exception as e:
#         logger.error(f"Error clearing ppt directory: {e}")
# 
# 
# async def create_presentation_from_prompt(prompt: str, session_id: str, slide_count: int = 5) -> list[dict]:
#     """Generates slide outline using Ollama, creates a PPTX presentation, and renders the slides."""
#     clear_ppt_directory()
# 
#     import httpx
# 
#     from backend.core.config import settings
# 
#     # system_content = (
#     #     "You are an expert presentation designer. Create a slide deck based on the user's topic.\n"
#     #     "You MUST respond ONLY with a valid JSON object. write some markdown also, "
#     #     "explanations, or backticks outside the JSON. The JSON must follow this exact schema:\n"
#     #     "{\n"
#     #     '  "slides": [\n'
#     #     "    {\n"
#     #     '      "title": "Slide Title",\n'
#     #     '      "bullets": ["Key bullet point 1", "Key bullet point 2"],\n'
#     #     '      "notes": "Detailed speaker notes for the presenter",\n'
#     #     '      "image_prompt": "Detailed description of a professional graphic, chart, 3D object, or diagram that visually represents this slide\'s content, or \'None\' if an image is not critically required. ONLY suggest an image prompt for slides that strictly need visual aids (like data charts, diagrams, or comparisons) to avoid unnecessary generation time. Use \'None\' for most text-only slides."\n'
#     #     "    }\n"
#     #     "  ]\n"
#     #     "}\n"
#     #     f"Generate exactly {slide_count} slides. Ensure the first slide is a high-impact Title Slide. "
#     #     "Keep bullets concise and professional (under 12 words each, max 4 bullets per slide)."
#     # )
#     system_content = (
#         "You are an expert presentation designer and business storyteller. "
#         "Create a professional, human-like slide deck based on the user's topic.\n\n"
# 
#         "Your response MUST be ONLY a valid JSON object. "
#         "Do not include markdown, explanations, code fences, or any text outside the JSON.\n\n"
# 
#         "The JSON must follow this exact schema:\n"
#         "{\n"
#         '  "slides": [\n'
#         "    {\n"
#         '      "title": "Slide Title",\n'
#         '      "bullets": ["Bullet 1 with short explanation", "Bullet 2 with short explanation", "Bullet 3 with short explanation", "Bullet 4 with short explanation", "Bullet 5 with short explanation"],\n'
#         '      "notes": "Detailed speaker notes for the presenter",\n'
#         '      "image_prompt": "Detailed visual prompt or None"\n'
#         "    }\n"
#         "  ]\n"
#         "}\n\n"
# 
#         f"Generate exactly {slide_count} slides.\n\n"
# 
#         "### Slide quality requirements:\n"
#         "1. The deck must feel like it was created by a skilled human consultant or presenter, not a sparse AI outline.\n"
#         "2. Every slide must contain enough meaningful content to stand on its own in a real presentation.\n"
#         "3. Avoid generic one-line bullets. Each bullet should communicate a complete insight, takeaway, or explanation.\n"
#         "4. Every slide must have exactly 4 to 5 bullets points. Do not write fewer than 4 bullets.\n"
#         "5. Keep bullet points concise but highly informative: ideally 10–18 words each. This provides enough explanation without flooding or overflowing the slide layout.\n"
#         "6. Make the deck flow like a story: introduction → context/problem → analysis/insights → recommendations/solution → conclusion.\n"
#         "7. Do not repeat the same idea across slides unless necessary for continuity.\n"
#         "8. Make each slide title specific and meaningful, not generic labels like 'Overview' unless justified.\n"
#         "9. Speaker notes must add substantial value: explain the bullets, give examples, context, transitions, and talking points.\n"
#         "10. Notes should be 120–250 words per slide so the presenter can confidently speak from them.\n"
#         "11. If the topic is technical, business, educational, or analytical, include structured reasoning, examples, comparisons, or implications in both bullets and notes.\n"
#         "12. Prefer concrete facts, frameworks, examples, trade-offs, and recommendations over vague statements.\n\n"
# 
#         "### Slide structure guidance:\n"
#         "- Slide 1 MUST be a high-impact title slide with subtitle-style bullets that frame the presentation.\n"
#         "- Middle slides should contain real content, not placeholders.\n"
#         "- Final slide should summarize key takeaways, decisions, recommendations, or next steps.\n\n"
# 
#         "### Bullet writing rules:\n"
#         "- Exactly 4 to 5 bullets per slide. Never exceed 5 bullets, never write fewer than 4.\n"
#         "- Each bullet should contain a short explanation or specific takeaway, not just a couple of keywords.\n"
#         "- Keep each bullet under 18 words to prevent layout overflow on the slide.\n"
#         "- Avoid filler phrases like 'important point' or 'some benefits'.\n"
#         "- Use parallel, professional phrasing.\n"
#         "- Bullets should sound natural and polished, as if prepared for a workplace, academic, or conference presentation.\n\n"
# 
#         "### Speaker notes rules:\n"
#         "- Notes must not simply repeat the bullets.\n"
#         "- Expand on why each point matters, include examples, transitions, and presenter guidance.\n"
#         "- Notes should make the slide feel fully developed and human-authored.\n"
#         "- If relevant, include mini case examples, interpretation of data, implementation suggestions, or likely audience questions.\n\n"
# 
#         "### Image prompt rules:\n"
#         "- Only suggest an image prompt for slides that strictly need visual aids (like data charts, diagrams, or comparisons) to avoid unnecessary generation time. Use 'None' for most text-only slides.\n"
#         "- If an image is needed, write a highly detailed professional prompt describing the exact visual to generate.\n"
#         "- Avoid decorative image prompts that do not add presentation value.\n\n"
# 
#         "### Output rules:\n"
#         "- Output valid JSON only.\n"
#         "- Do not wrap JSON in markdown.\n"
#         "- Do not include comments.\n"
#         "- Do not omit notes or image_prompt fields."
#     )
# 
#     url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat"
#     payload = {
#         "model": "qwen2.5:3b",
#         "stream": False,
#         "messages": [
#             {"role": "system", "content": system_content},
#             {"role": "user", "content": f"Create a presentation on the topic: {prompt}"},
#         ],
#         "options": {"temperature": 0.3, "num_predict": 1500},
#         "format": "json",
#     }
# 
#     slides_data = []
#     parsed = None
# 
#     # 1. Try local Ollama (Priority 1)
#     try:
#         logger.info("Generating slides using local Ollama model (qwen2.5:3b)...")
#         async with httpx.AsyncClient(timeout=60.0) as client:
#             resp = await client.post(url, json=payload)
#             if resp.status_code == 200:
#                 result_data = resp.json()
#                 raw_content = result_data.get("message", {}).get("content", "").strip()
# 
#                 # Parse JSON
#                 try:
#                     parsed = json.loads(raw_content)
#                 except Exception:
#                     json_match = re.search(r"(\{.*\}|\[.*\])", raw_content, re.DOTALL)
#                     if json_match:
#                         parsed = json.loads(json_match.group(1))
#                     else:
#                         raise
#     except Exception as ollama_err:
#         logger.error(f"Local Ollama slide generation failed: {ollama_err}. Falling back to Groq...")
# 
#     # 2. Try Groq (Llama 3.3 70B - Fallback 2)
#     if not parsed and settings.GROQ_API_KEY:
#         try:
#             resp = await client.chat.completions.create(
#                 model="llama-3.3-70b-versatile",
#                 messages=[
#                     {"role": "system", "content": system_content},
#                     {"role": "user", "content": f"Create a presentation outline on the topic: {prompt}"},
#                 ],
#                 response_format={"type": "json_object"},
#             )
#             parsed = json.loads(resp.choices[0].message.content)
#         except Exception as groq_err:
#             logger.warning(f"Cloud Groq slide generation failed: {groq_err}. Falling back to Gemini...")
# 
#     # 3. Try Gemini (Highly detailed, content-rich - Fallback 3)
#     if not parsed and settings.GEMINI_API_KEY:
#         try:
#             logger.info("Generating content-rich slides using cloud Gemini API...")
#             import google.generativeai as genai
# 
#             genai.configure(api_key=settings.GEMINI_API_KEY)
#             model = genai.GenerativeModel("gemini-2.5-flash")
# 
#             response = await model.generate_content_async(
#                 f"Create a presentation outline on the topic: {prompt}",
#                 generation_config={
#                     "response_mime_type": "application/json",
#                     "system_instruction": system_content,
#                 }
#             )
#             parsed = json.loads(response.text.strip())
#         except Exception as gem_err:
#             logger.warning(f"Cloud Gemini slide generation failed: {gem_err}.")
# 
#     # Normalize parsed JSON output into standard slide outline list
#     try:
#         if not parsed:
#             raise ValueError("No slide data was successfully generated by any LLM provider.")
# 
#         # Robustly extract the list of slides
#         extracted_list = []
#         if isinstance(parsed, dict):
#             # Find any list within the dictionary
#             for key, val in parsed.items():
#                 if isinstance(val, list):
#                     extracted_list = val
#                     break
#             if not extracted_list and "title" in parsed:
#                 extracted_list = [parsed]
#         elif isinstance(parsed, list):
#             extracted_list = parsed
# 
#         # Normalize each slide item in the list
#         normalized_slides = []
#         for i, item in enumerate(extracted_list):
#             if isinstance(item, dict):
#                 bullets = item.get("bullets", [])
#                 if isinstance(bullets, str):
#                     bullets = [bullets]
#                 elif not isinstance(bullets, list):
#                     bullets = []
#                 bullets = [str(b) for b in bullets]
# 
#                 # Retrieve the optional generated image prompt and download the AI asset
#                 image_prompt = item.get("image_prompt", "").strip()
#                 title_str = str(item.get("title", "Untitled Slide"))
# 
#                 # Enforce visual asset generation for all content slides (slides after the title slide)
#                 if i > 0:
#                     if not image_prompt or image_prompt.lower() in ("none", "null", ""):
#                         image_prompt = (
#                             f"A clean, professional modern corporate graphic representation of: {title_str}"
#                         )
# 
#                 slide_images = []
#                 if image_prompt and image_prompt.lower() not in ("none", "null", ""):
#                     img_path = await download_ai_image(image_prompt)
#                     if img_path:
#                         slide_images.append(
#                             {
#                                 "path": img_path,
#                                 "left_in": 7.5,
#                                 "top_in": 1.8,
#                                 "width_in": 5.0,
#                                 "height_in": 4.5,
#                             }
#                         )
# 
#                 normalized_slides.append(
#                     {
#                         "title": title_str,
#                         "bullets": bullets,
#                         "notes": str(item.get("notes", "")),
#                         "images": slide_images,
#                     }
#                 )
#             elif isinstance(item, str):
#                 normalized_slides.append({"title": item, "bullets": [], "notes": "", "images": []})
# 
#         if normalized_slides:
#             slides_data = normalized_slides
#         else:
#             raise ValueError("No valid slides parsed from JSON")
#     except Exception as e:
#         logger.error(f"Failed to normalize slide data: {e}")
#         # Fallback slides in case LLM generation fails
#         slides_data = [
#             {
#                 "title": f"Introduction to {prompt}",
#                 "bullets": [
#                     f"Overview of {prompt}",
#                     "Key concepts and foundations",
#                     "Historical background and context",
#                 ],
#                 "notes": "Welcome everyone. Today we are exploring this topic.",
#             },
#             {
#                 "title": "Core Principles",
#                 "bullets": [
#                     "Fundamental mechanism of action",
#                     "Key structures and components",
#                     "Important rules and guidelines",
#                 ],
#                 "notes": "Let's dive into the core mechanisms that make this work.",
#             },
#             {
#                 "title": "Main Benefits & Applications",
#                 "bullets": [
#                     "Real-world use cases",
#                     "Efficiency and cost improvements",
#                     "Impact on modern industry",
#                 ],
#                 "notes": "Here are the primary ways this technology is applied in the real world.",
#             },
#             {
#                 "title": "Challenges & Solutions",
#                 "bullets": [
#                     "Current limitations and hurdles",
#                     "Innovative workarounds and research",
#                     "Future outlook and developments",
#                 ],
#                 "notes": "Of course, there are some challenges we must address.",
#             },
#             {
#                 "title": "Conclusion & Q&A",
#                 "bullets": [
#                     "Summary of key takeaways",
#                     "Future roadmap and expansion",
#                     "Thank you for your time",
#                 ],
#                 "notes": "Thank you. I am happy to open the floor for any questions.",
#             },
#         ]
#         slides_data = slides_data[:slide_count]
# 
#     # Save and render presentation
#     store_list = save_and_render_pptx(slides_data, session_id, prompt)
#     _current_slide[session_id] = 0
#     return store_list
# 
# 
# def save_and_render_pptx(slides_data: list[dict], session_id: str, topic: str = "Presentation") -> list[dict]:
#     """Compiles list of slide dictionaries to PPTX presentation and renders each slide to base64 PNG."""
#     from pptx import Presentation
#     from pptx.dml.color import RGBColor
#     from pptx.enum.text import PP_ALIGN
#     from pptx.util import Inches, Pt
# 
#     template_path = "/Users/pagupta/Desktop/capstone_major_sare_hai/PILOT/GD Presentation template.pptx"
#     templated = False
#     if os.path.exists(template_path):
#         try:
#             prs = Presentation(template_path)
#             # Delete all existing slides in the template
#             # Iterate backwards to avoid index shifting issues
#             for i in range(len(prs.slides) - 1, -1, -1):
#                 rId = prs.slides._sldIdLst[i].rId
#                 prs.part.drop_rel(rId)
#                 del prs.slides._sldIdLst[i]
#             templated = True
#             logger.info("Loaded and cleaned slide template from GD Presentation template.pptx")
#         except Exception as e:
#             logger.error(f"Failed to load slide template: {e}. Falling back to default.")
#             prs = Presentation()
#     else:
#         prs = Presentation()
# 
#     if not templated:
#         prs.slide_width = Inches(13.333)
#         prs.slide_height = Inches(7.5)
#     blank_layout = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[0]
# 
#     for idx, slide_item in enumerate(slides_data):
#         if idx == 0:
#             # Title slide layout
#             if templated and len(prs.slide_layouts) > 0:
#                 slide = prs.slides.add_slide(prs.slide_layouts[0])
#             else:
#                 slide = prs.slides.add_slide(blank_layout)
#         else:
#             # Content slide layout
#             if templated and len(prs.slide_layouts) > 2:
#                 slide = prs.slides.add_slide(prs.slide_layouts[2])
#             else:
#                 slide = prs.slides.add_slide(blank_layout)
# 
#         # Background and accent decorators — ONLY draw if not using a slide layout template
#         if not templated:
#             # Widescreen left accent bar: left 0, top 0, width 0.12 inches, height 7.5 inches (Grid Dynamics Accent Bar)
#             accent_bar = slide.shapes.add_shape(
#                 1,  # MSO_SHAPE.RECTANGLE = 1
#                 Inches(0),
#                 Inches(0),
#                 Inches(0.12),
#                 Inches(7.5),
#             )
#             accent_bar.fill.solid()
#             accent_bar.fill.fore_color.rgb = RGBColor(245, 167, 0)  # Amber Gold (#F5A700)
#             accent_bar.line.fill.background()
# 
#             # Background solid color: White (#FFFFFF)
#             background = slide.background
#             fill = background.fill
#             fill.solid()
#             fill.fore_color.rgb = RGBColor(255, 255, 255)
# 
#         title_text = slide_item.get("title", f"Slide {idx + 1}")
#         bullets = slide_item.get("bullets", [])
#         notes = slide_item.get("notes", "")
# 
#         # Extract font style overrides
#         font_name = slide_item.get("font_name", "Arial")
#         title_bold = slide_item.get("title_bold", True)
#         title_italic = slide_item.get("title_italic", False)
#         bullet_bold = slide_item.get("bullet_bold", False)
#         bullet_italic = slide_item.get("bullet_italic", False)
# 
#         # Check if this slide has any valid images on disk
#         has_images = False
#         images = slide_item.get("images", [])
#         if images and isinstance(images, list):
#             for img_item in images:
#                 img_path = img_item.get("path")
#                 if img_path and os.path.exists(img_path):
#                     has_images = True
#                     break
# 
#         if idx == 0:
#             # Title slide layout
#             title_ph = None
#             subtitle_ph = None
#             if templated:
#                 for ph in slide.placeholders:
#                     if ph.placeholder_format.idx == 0:
#                         title_ph = ph
#                     elif ph.placeholder_format.idx == 2:
#                         subtitle_ph = ph
# 
#             if title_ph:
#                 tf = title_ph.text_frame
#             else:
#                 tx_box = slide.shapes.add_textbox(Inches(1.0), Inches(2.0), Inches(11.333), Inches(4.0))
#                 tf = tx_box.text_frame
#                 tf.word_wrap = True
# 
#             # Title text
#             p = tf.paragraphs[0]
#             p.text = title_text
#             p.alignment = PP_ALIGN.CENTER
#             p.font.name = font_name
#             p.font.size = Pt(48)
#             p.font.bold = title_bold
#             p.font.italic = title_italic
#             p.font.color.rgb = RGBColor(17, 17, 18)  # Dark Charcoal
# 
#             # Subtitle (Darker Amber Gold for contrast)
#             if subtitle_ph:
#                 tf2 = subtitle_ph.text_frame
#                 p2 = tf2.paragraphs[0]
#                 p2.text = "Presentation Workspace"
#                 p2.alignment = PP_ALIGN.CENTER
#                 p2.font.name = font_name
#                 p2.font.size = Pt(22)
#                 p2.font.bold = False
#                 p2.font.italic = True
#                 p2.font.color.rgb = RGBColor(212, 144, 15)  # Dark Amber Gold
#             elif not templated:
#                 p2 = tf.add_paragraph()
#                 p2.text = "Presentation Workspace"
#                 p2.alignment = PP_ALIGN.CENTER
#                 p2.font.name = font_name
#                 p2.font.size = Pt(22)
#                 p2.font.bold = False
#                 p2.font.italic = True
#                 p2.font.color.rgb = RGBColor(212, 144, 15)  # Dark Amber Gold
#                 p2.space_before = Pt(20)
#         else:
#             # Content slide layout
#             title_ph = None
#             body_ph = None
#             if templated:
#                 for ph in slide.placeholders:
#                     if ph.placeholder_format.idx == 0:
#                         title_ph = ph
#                     elif ph.placeholder_format.idx == 1:
#                         body_ph = ph
# 
#             # Title at the top-left (Darker Amber Gold for contrast)
#             slide_w_in = prs.slide_width.inches
#             if title_ph:
#                 tf_title = title_ph.text_frame
#             else:
#                 title_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.6), Inches(slide_w_in - 1.6), Inches(1.0))
#                 tf_title = title_box.text_frame
#                 tf_title.word_wrap = True
# 
#             p_title = tf_title.paragraphs[0]
#             p_title.text = title_text
#             p_title.font.name = font_name
#             p_title.font.size = Pt(32)
#             p_title.font.bold = title_bold
#             p_title.font.italic = title_italic
#             p_title.font.color.rgb = RGBColor(212, 144, 15)  # Dark Amber Gold
# 
#             # Divider line decorative element (Amber Gold) - ONLY draw if not templated
#             if not templated:
#                 divider = slide.shapes.add_shape(
#                     1,  # MSO_SHAPE.RECTANGLE = 1
#                     Inches(0.8),
#                     Inches(1.4),
#                     Inches(11.733),
#                     Inches(0.02),
#                 )
#                 divider.fill.solid()
#                 divider.fill.fore_color.rgb = RGBColor(245, 167, 0)  # Amber Gold (#F5A700)
#                 divider.line.fill.background()
# 
#             # Bullet points — narrow width if slide contains an image
#             slide_w_in = prs.slide_width.inches
#             if body_ph:
#                 if has_images:
#                     body_ph.width = Inches(slide_w_in * 0.5)
#                 tf_content = body_ph.text_frame
#             else:
#                 content_width = Inches(slide_w_in * 0.5) if has_images else Inches(slide_w_in - 1.6)
#                 content_box = slide.shapes.add_textbox(Inches(0.8), Inches(1.8), content_width, Inches(4.5))
#                 tf_content = content_box.text_frame
#                 tf_content.word_wrap = True
# 
#             # Limit bullets to at most 5 items to guarantee clean layout boundaries
#             visible_bullets = bullets[:5]
#             num_bullets = len(visible_bullets)
#             total_chars = sum(len(b) for b in visible_bullets)
# 
#             # Dynamically compute optimal font size and spacing based on copy volume
#             if num_bullets >= 5 or total_chars > 320:
#                 font_size = Pt(13)
#                 space_after = Pt(6)
#             elif num_bullets == 4 or total_chars > 220:
#                 font_size = Pt(15)
#                 space_after = Pt(8)
#             elif num_bullets == 3:
#                 font_size = Pt(17)
#                 space_after = Pt(10)
#             else:
#                 font_size = Pt(20)
#                 space_after = Pt(14)
# 
#             tf_content.clear()
#             for b_idx, b in enumerate(visible_bullets):
#                 p_bullet = tf_content.paragraphs[0] if (b_idx == 0 and len(tf_content.paragraphs) > 0) else tf_content.add_paragraph()
#                 if templated:
#                     p_bullet.text = b
#                     p_bullet.level = 0
#                 else:
#                     p_bullet.text = f"•  {b}"
#                 p_bullet.font.name = font_name
#                 p_bullet.font.size = font_size
#                 p_bullet.font.bold = bullet_bold
#                 p_bullet.font.italic = bullet_italic
#                 p_bullet.font.color.rgb = RGBColor(40, 40, 42)  # Dark Charcoal
#                 p_bullet.space_after = space_after
# 
#             # Insert images on the right side if present
#             if has_images:
#                 slide_w_in = prs.slide_width.inches
#                 scale = slide_w_in / 13.333
#                 for img_item in images:
#                     img_path = img_item.get("path")
#                     if img_path and os.path.exists(img_path):
#                         try:
#                             left_val = img_item.get("left_in", 7.5) * scale
#                             top_val = img_item.get("top_in", 1.8)
#                             w_val = img_item.get("width_in", 5.0) * scale
#                             h_val = img_item.get("height_in", 4.5)
#                             slide.shapes.add_picture(
#                                 img_path,
#                                 Inches(left_val),
#                                 Inches(top_val),
#                                 width=Inches(w_val),
#                                 height=Inches(h_val),
#                             )
#                         except Exception as img_err:
#                             logger.error(f"Failed to add picture {img_path} to slide {idx + 1}: {img_err}")
# 
#             if not templated:
#                 # Subtle footer divider
#                 footer_line = slide.shapes.add_shape(1, Inches(0.8), Inches(6.7), Inches(11.733), Inches(0.015))
#                 footer_line.fill.solid()
#                 footer_line.fill.fore_color.rgb = RGBColor(220, 220, 225)  # Light Gray
#                 footer_line.line.fill.background()
# 
#                 # Slide number + branded footer (Grid Dynamics)
#                 footer_box = slide.shapes.add_textbox(Inches(8.0), Inches(6.8), Inches(4.533), Inches(0.5))
#                 p_foot = footer_box.text_frame.paragraphs[0]
#                 p_foot.text = f"Grid Dynamics  |  Slide {idx + 1} of {len(slides_data)}"
#                 p_foot.alignment = PP_ALIGN.RIGHT
#                 p_foot.font.name = "Arial"
#                 p_foot.font.size = Pt(9)
#                 p_foot.font.color.rgb = RGBColor(110, 110, 115)  # Medium Gray
# 
#                 # Topic on bottom left
#                 topic_box = slide.shapes.add_textbox(Inches(0.8), Inches(6.8), Inches(6.0), Inches(0.5))
#                 p_topic = topic_box.text_frame.paragraphs[0]
#                 p_topic.text = f"Topic: {topic.title()}"
#                 p_topic.font.name = "Arial"
#                 p_topic.font.size = Pt(9)
#                 p_topic.font.color.rgb = RGBColor(142, 142, 147)  # Warm Gray
# 
#         # Set speaker notes
#         if notes:
#             try:
#                 if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
#                     slide.notes_slide.notes_text_frame.text = notes
#             except Exception:
#                 pass
# 
#     # Save presentation
#     os.makedirs("data/ppt", exist_ok=True)
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     prs.save(pptx_path)
# 
#     # Render the presentation to PNGs
#     import fitz
# 
#     try:
#         if hasattr(fitz, "TOOLS"):
#             fitz.TOOLS.mupdf_display_errors(False)
#     except Exception:
#         pass
# 
#     prs_w = prs.slide_width
#     prs_h = prs.slide_height
#     store_list = []
# 
#     soffice_path = _get_libreoffice_path()
#     pdf_doc = None
# 
#     if soffice_path:
#         try:
#             temp_dir = tempfile.mkdtemp()
#             _run_libreoffice_headless(soffice_path, pptx_path, temp_dir, timeout=15.0)
# 
#             pdf_filename = f"{session_id}.pdf"
#             pdf_path = os.path.join(temp_dir, pdf_filename)
#             if os.path.exists(pdf_path):
#                 pdf_doc = fitz.open(pdf_path)
#         except Exception as lo_err:
#             logger.error(f"AI presentation LibreOffice PDF conversion failed: {lo_err}")
# 
#     for i, slide in enumerate(prs.slides):
#         meta = _extract_slide_meta(slide, i, prs_w, prs_h)
#         img_b64 = ""
# 
#         if pdf_doc and i < len(pdf_doc):
#             try:
#                 page = pdf_doc[i]
#                 pix = page.get_pixmap(dpi=120)
#                 png_bytes = pix.tobytes("png")
#                 img_b64 = base64.b64encode(png_bytes).decode()
#             except Exception as page_err:
#                 logger.error(f"Failed to render generated slide page {i} from PDF: {page_err}")
# 
#         if not img_b64:
#             img_b64 = _slide_to_png_b64(slide, prs_w, prs_h)
# 
#         # Preserve the images data structure in memory store
#         slide_images = slides_data[i].get("images", []) if i < len(slides_data) else []
#         store_list.append({**meta, "img_b64": img_b64, "images": slide_images})
# 
#     if pdf_doc:
#         pdf_doc.close()
#         try:
#             shutil.rmtree(temp_dir, ignore_errors=True)
#         except Exception:
#             pass
# 
#     _slide_store[session_id] = store_list
#     set_latest_upload_sid(session_id)
#     return store_list
# 
# 
# async def improvise_slide_content(current_slide: dict, user_prompt: str) -> dict:
#     """Invokes LLM (Gemini -> Groq -> local Ollama cascade) to rewrite the content of a single slide."""
#     import httpx
# 
#     from backend.core.config import settings
# 
#     system_content = (
#         "You are an expert presentation improver. Your job is to rewrite the content of a SINGLE slide based on the user's improvisation request.\n"
#         "Ensure the slide content is extremely comprehensive, informative, highly detailed, and professional (avoid simple short one-word or brief bullet points). "
#         "Each bullet point must be a well-structured, complete, multi-sentence statement (at least 20-30 words) containing specific facts, concrete data, or detailed reasoning.\n"
#         "IMPORTANT: Even if the user's request is to 'simplify', 'summarize', 'make concise', or 'shorten', you MUST NOT produce brief, low-content, or vague bullet points. "
#         "Instead, rewrite using clear and highly readable language, but maintain a good amount  of information for explanation and a good amount of content—each bullet point must remain a fully developed, detailed sentence or two explaining a complete concept. Never output short phrases or single-word bullets.\n"
#         "You MUST respond ONLY with a valid JSON object matching this exact schema:\n"
#         "{\n"
#         '  "title": "Improved Slide Title",\n'
#         '  "bullets": ["Highly detailed, informative, and comprehensive bullet point 1", "Highly detailed, informative, and comprehensive bullet point 2"],\n'
#         '  "notes": "Comprehensive and detailed speaker notes fully detailing the concepts shown on the slide"\n'
#         "}\n"
#         "Do not write any markdown, explanations, or backticks outside the JSON.\n"
#         "Generate 3-5 comprehensive and rich bullet points that fully cover the subject matter."
#     )
# 
#     current_desc = (
#         f"Current Slide Content:\n"
#         f"Title: {current_slide.get('title', 'Untitled')}\n"
#         f"Bullets: {current_slide.get('bullets', [])}\n"
#         f"Notes: {current_slide.get('notes', '')}\n"
#     )
# 
#     user_content = (
#         f"{current_desc}\n"
#         f"Improvisation Request: {user_prompt}\n\n"
#         f"Please rewrite the slide according to the request."
#     )
# 
#     parsed = None
# 
#     # 1. Try Gemini
#     if settings.GEMINI_API_KEY:
#         try:
#             import google.generativeai as genai
# 
#             genai.configure(api_key=settings.GEMINI_API_KEY)
#             model = genai.GenerativeModel("gemini-2.5-flash")
#             response = await model.generate_content_async(
#                 user_content,
#                 generation_config={
#                     "response_mime_type": "application/json",
#                     "system_instruction": system_content,
#                 },
#             )
#             parsed = json.loads(response.text.strip())
#         except Exception as e:
#             logger.warning(f"Cloud Gemini slide improvisation failed: {e}")
# 
#     # 2. Try Groq
#     if not parsed and settings.GROQ_API_KEY:
#         try:
#             from groq import AsyncGroq
# 
#             client = AsyncGroq(api_key=settings.GROQ_API_KEY)
#             resp = await client.chat.completions.create(
#                 model="llama-3.3-70b-versatile",
#                 messages=[
#                     {"role": "system", "content": system_content},
#                     {"role": "user", "content": user_content},
#                 ],
#                 response_format={"type": "json_object"},
#             )
#             parsed = json.loads(resp.choices[0].message.content)
#         except Exception as e:
#             logger.warning(f"Cloud Groq slide improvisation failed: {e}")
# 
#     # 3. Fallback to local Ollama
#     if not parsed:
#         try:
#             url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat"
#             payload = {
#                 "model": settings.OLLAMA_MODEL,
#                 "stream": False,
#                 "messages": [
#                     {"role": "system", "content": system_content},
#                     {"role": "user", "content": user_content},
#                 ],
#                 "options": {"temperature": 0.3, "num_predict": 1500},
#                 "format": "json",
#             }
#             async with httpx.AsyncClient(timeout=40.0) as client:
#                 resp = await client.post(url, json=payload)
#                 if resp.status_code == 200:
#                     result_data = resp.json()
#                     raw_content = result_data.get("message", {}).get("content", "").strip()
#                     try:
#                         parsed = json.loads(raw_content)
#                     except Exception:
#                         json_match = re.search(r"(\{.*\})", raw_content, re.DOTALL)
#                         if json_match:
#                             parsed = json.loads(json_match.group(1))
#         except Exception as e:
#             logger.error(f"Local Ollama slide improvisation failed: {e}")
# 
#     # Normalize output
#     if parsed and isinstance(parsed, dict):
#         bullets = parsed.get("bullets", [])
#         if isinstance(bullets, str):
#             bullets = [bullets]
#         elif not isinstance(bullets, list):
#             bullets = []
#         return {
#             "title": str(parsed.get("title", current_slide.get("title", "Untitled"))),
#             "bullets": [str(b) for b in bullets],
#             "notes": str(parsed.get("notes", current_slide.get("notes", ""))),
#         }
# 
#     # Unmodified fallback
#     return {
#         "title": current_slide.get("title", "Untitled"),
#         "bullets": current_slide.get("bullets", []),
#         "notes": current_slide.get("notes", ""),
#     }
# 
# 
# 
# # ---- route handlers + pptx-editing helpers superseded by the new implementation below (kept for reference) ----
# # class AddSlideCmd(BaseModel):
# #     session_id: str
# #     title: Optional[str] = None
# #     bullets: Optional[List[str]] = None
# #     notes: Optional[str] = None
# # 
# # 
# # class EditSlideCmd(BaseModel):
# #     session_id: str
# #     slide_index: int
# #     title: Optional[str] = None
# #     bullets: Optional[List[str]] = None
# #     add_bullet: Optional[str] = None
# #     notes: Optional[str] = None
# #     images: Optional[List[dict]] = None
# # 
# # 
# # class ClearCmd(BaseModel):
# #     session_id: str
# # 
# # 
# # class DeleteSlideCmd(BaseModel):
# #     session_id: str
# #     slide_index: int
# # 
# # 
# # class ImproviseSlideCmd(BaseModel):
# #     session_id: str
# #     slide_index: int
# #     prompt: str
# # 
# # 
# # def _add_slide_in_pptx(pptx_path: str, title: str, bullets: list[str], notes: str | None = None):
# #     from pptx import Presentation
# #     from pptx.util import Inches, Pt
# #     from pptx.dml.color import RGBColor
# #     
# #     if not os.path.exists(pptx_path):
# #         return
# #         
# #     prs = Presentation(pptx_path)
# #     blank_layout = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[0]
# #     slide = prs.slides.add_slide(blank_layout)
# #     
# #     # Title
# #     title_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.6), Inches(11.7), Inches(1.0))
# #     tf_title = title_box.text_frame
# #     p_title = tf_title.paragraphs[0]
# #     p_title.text = title
# #     p_title.font.size = Pt(32)
# #     p_title.font.bold = True
# #     p_title.font.color.rgb = RGBColor(245, 167, 0)
# #     
# #     # Bullets
# #     content_box = slide.shapes.add_textbox(Inches(0.8), Inches(1.8), Inches(11.7), Inches(4.5))
# #     tf_content = content_box.text_frame
# #     tf_content.word_wrap = True
# #     for i, b in enumerate(bullets):
# #         p_bullet = tf_content.paragraphs[0] if i == 0 else tf_content.add_paragraph()
# #         p_bullet.text = f"•  {b}" if not b.startswith("•") else b
# #         p_bullet.font.size = Pt(20)
# #         p_bullet.font.color.rgb = RGBColor(0, 0, 0)
# #         p_bullet.space_after = Pt(10)
# #         
# #     if notes:
# #         try:
# #             if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
# #                 slide.notes_slide.notes_text_frame.text = notes
# #         except Exception:
# #             pass
# #             
# #     prs.save(pptx_path)
# # 
# # 
# # def _delete_slide_in_pptx(pptx_path: str, slide_index: int):
# #     from pptx import Presentation
# #     
# #     if not os.path.exists(pptx_path):
# #         return
# #         
# #     prs = Presentation(pptx_path)
# #     if slide_index < 0 or slide_index >= len(prs.slides):
# #         return
# #         
# #     # Delete slide from relationship list
# #     id_list = prs.slides._sldIdLst
# #     slide_id = prs.slides[slide_index].slide_id
# #     for slide_element in id_list:
# #         if slide_element.id == slide_id:
# #             id_list.remove(slide_element)
# #             break
# #             
# #     prs.save(pptx_path)
# # 
# # 
# # def _update_slide_in_pptx(pptx_path: str, slide_index: int, title: str | None, bullets: list[str] | None, notes: str | None, images: list[dict] | None = None):
# #     from pptx import Presentation
# #     from pptx.util import Inches, Pt
# #     from pptx.dml.color import RGBColor
# #     import re
# #     
# #     if not os.path.exists(pptx_path):
# #         return
# #         
# #     prs = Presentation(pptx_path)
# #     if slide_index < 0 or slide_index >= len(prs.slides):
# #         return
# #         
# #     slide = prs.slides[slide_index]
# #     _clear_corrupt_footer_placeholders(slide)
# #     
# #     # 1. Update Title if provided, preserving original formatting runs
# #     if title is not None:
# #         title_updated = False
# #         try:
# #             if slide.shapes.title:
# #                 tf_title = slide.shapes.title.text_frame
# #                 if tf_title.paragraphs and tf_title.paragraphs[0].runs:
# #                     tf_title.paragraphs[0].runs[0].text = title
# #                     for r in list(tf_title.paragraphs[0].runs[1:]):
# #                         r_el = r._r
# #                         r_el.getparent().remove(r_el)
# #                 else:
# #                     slide.shapes.title.text = title
# #                 title_updated = True
# #         except Exception:
# #             pass
# #             
# #         if not title_updated:
# #             for shape in slide.shapes:
# #                 if shape.has_text_frame and shape.top < 1371600:
# #                     tf_title = shape.text_frame
# #                     if tf_title.paragraphs and tf_title.paragraphs[0].runs:
# #                         tf_title.paragraphs[0].runs[0].text = title
# #                         for r in list(tf_title.paragraphs[0].runs[1:]):
# #                             r_el = r._r
# #                             r_el.getparent().remove(r_el)
# #                     else:
# #                         shape.text_frame.text = title
# #                     title_updated = True
# #                     break
# #                     
# #         if not title_updated:
# #             title_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.6), Inches(11.7), Inches(1.0))
# #             tf_title = title_box.text_frame
# #             p_title = tf_title.paragraphs[0]
# #             p_title.text = title
# #             p_title.font.size = Pt(32)
# #             p_title.font.bold = True
# #             p_title.font.color.rgb = RGBColor(245, 167, 0)  # Amber Gold (#F5A700)
# #             
# #     # 2. Update bullets by replacing the real content frame.
# #     # Older saves could leave editor-created textboxes behind; remove those first
# #     # so deleted bullets cannot reappear in the preview or OnlyOffice.
# #     if bullets is not None:
# #         from pptx.util import Inches, Pt
# #         from pptx.dml.color import RGBColor
# #         from pptx.enum.shapes import PP_PLACEHOLDER
# # 
# #         clean_bullets = []
# #         for b in bullets:
# #             text = _clean_bullet_text(str(b or ""))
# #             if text:
# #                 clean_bullets.append(text)
# # 
# #         def remove_shape(shape):
# #             el = shape._element
# #             el.getparent().remove(el)
# # 
# #         for shape in list(slide.shapes):
# #             try:
# #                 if getattr(shape, "name", "") == "PilotEditorBullets":
# #                     remove_shape(shape)
# #             except Exception:
# #                 pass
# # 
# #         bullets_updated = False
# #         main_content_shape = _get_primary_bullet_shape(slide)
# # 
# #         if main_content_shape is not None:
# #             tf = main_content_shape.text_frame
# #             tf.clear()  # clears text, leaves 1 empty paragraph
# #             tf.word_wrap = True
# #             for i, b in enumerate(clean_bullets):
# #                 p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
# #                 if main_content_shape.is_placeholder:
# #                     p.text = b
# #                     p.level = 0
# #                 else:
# #                     p.text = b if b.startswith("•") else f"•  {b}"
# #                 p.font.size = Pt(16)
# #                 p.space_after = Pt(6)
# #             bullets_updated = True
# # 
# #         # Fallback: create one editor-owned textbox, still replacing its content.
# #         if not bullets_updated:
# #             if clean_bullets:
# #                 content_box = slide.shapes.add_textbox(
# #                     Inches(0.5), Inches(2.2), Inches(11.7), Inches(4.5)
# #                 )
# #                 content_box.name = "PilotEditorBullets"
# #                 tf = content_box.text_frame
# #                 tf.word_wrap = True
# # 
# #                 for i, b in enumerate(clean_bullets):
# #                     p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
# #                     p.text = b if b.startswith("•") else f"•  {b}"
# #                     p.font.size = Pt(16)
# #                     p.space_after = Pt(6)
# # 
# #     # 3. Update images if provided
# #     if images is not None:
# #         # Extract session_id from pptx_path
# #         session_id = os.path.splitext(os.path.basename(pptx_path))[0]
# #         
# #         # Get the original images list for this slide index from the slide store cache
# #         old_slides = _slide_store.get(session_id, [])
# #         original_images = []
# #         if slide_index < len(old_slides):
# #             original_images = old_slides[slide_index].get("images", [])
# # 
# #         # Step 1: Detect and remove deleted images from slide.shapes
# #         for shape in list(slide.shapes):
# #             try:
# #                 if shape.shape_type == 13 or shape.__class__.__name__ == "Picture" or hasattr(shape, "image"):
# #                     shape_left = shape.left / 914400.0
# #                     shape_top = shape.top / 914400.0
# #                     shape_width = shape.width / 914400.0
# #                     shape_height = shape.height / 914400.0
# # 
# #                     # Check if this shape matches any original image
# #                     is_user_image = False
# #                     was_deleted = False
# #                     
# #                     for o_img in original_images:
# #                         o_left = o_img.get("left_in", 0)
# #                         o_top = o_img.get("top_in", 0)
# #                         o_width = o_img.get("width_in", 0)
# #                         o_height = o_img.get("height_in", 0)
# # 
# #                         if (
# #                             abs(shape_left - o_left) < 0.15
# #                             and abs(shape_top - o_top) < 0.15
# #                             and abs(shape_width - o_width) < 0.15
# #                             and abs(shape_height - o_height) < 0.15
# #                         ):
# #                             is_user_image = True
# #                             
# #                             # Check if it is missing in the new images list
# #                             found_in_new = False
# #                             for n_img in images:
# #                                 n_left = n_img.get("left_in", 0)
# #                                 n_top = n_img.get("top_in", 0)
# #                                 n_width = n_img.get("width_in", 0)
# #                                 n_height = n_img.get("height_in", 0)
# #                                 if (
# #                                     abs(n_left - o_left) < 0.15
# #                                     and abs(n_top - o_top) < 0.15
# #                                     and abs(n_width - o_width) < 0.15
# #                                     and abs(n_height - o_height) < 0.15
# #                                 ):
# #                                     found_in_new = True
# #                                     break
# #                             
# #                             if not found_in_new:
# #                                 was_deleted = True
# #                             break
# # 
# #                     if is_user_image and was_deleted:
# #                         el = shape._element
# #                         el.getparent().remove(el)
# #                         logger.info(f"Removed deleted image shape at left={shape_left:.2f}, top={shape_top:.2f}")
# #             except Exception as e:
# #                 logger.error(f"Error checking/deleting picture shape: {e}")
# # 
# #         # Step 2: Add any new images that are not already present on the slide
# #         for img in images:
# #             img_path = img.get("path")
# #             if img_path and os.path.exists(img_path):
# #                 # Check if this new image is already present on the slide
# #                 already_exists = False
# #                 n_left = img.get("left_in", 0)
# #                 n_top = img.get("top_in", 0)
# #                 n_width = img.get("width_in", 0)
# #                 n_height = img.get("height_in", 0)
# # 
# #                 for shape in slide.shapes:
# #                     if shape.shape_type == 13 or shape.__class__.__name__ == "Picture" or hasattr(shape, "image"):
# #                         shape_left = shape.left / 914400.0
# #                         shape_top = shape.top / 914400.0
# #                         shape_width = shape.width / 914400.0
# #                         shape_height = shape.height / 914400.0
# # 
# #                         if (
# #                             abs(shape_left - n_left) < 0.15
# #                             and abs(shape_top - n_top) < 0.15
# #                             and abs(shape_width - n_width) < 0.15
# #                             and abs(shape_height - n_height) < 0.15
# #                         ):
# #                             already_exists = True
# #                             break
# # 
# #                 if not already_exists:
# #                     try:
# #                         left = img.get("left_in", 7.5)
# #                         top = img.get("top_in", 1.8)
# #                         width = img.get("width_in", 5.0)
# #                         height = img.get("height_in", 4.5)
# #                         slide.shapes.add_picture(
# #                             img_path,
# #                             Inches(left),
# #                             Inches(top),
# #                             width=Inches(width),
# #                             height=Inches(height)
# #                         )
# #                         logger.info(f"Added new picture shape at left={left:.2f}, top={top:.2f}")
# #                     except Exception as e:
# #                         logger.error(f"Error adding picture: {e}")
# #                 
# #     # 4. Update notes if provided
# #     if notes is not None:
# #         try:
# #             if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
# #                 slide.notes_slide.notes_text_frame.text = notes
# #         except Exception:
# #             pass
# #             
# #     prs.save(pptx_path)
# # 
# # 
# # def _re_render_pptx(session_id: str, old_slides: list[dict]) -> list[dict]:
# #     from pptx import Presentation
# #     import fitz
# #     import shutil
# #     import tempfile
# #     import subprocess
# #     
# #     pptx_path = f"data/ppt/{session_id}.pptx"
# #     if not os.path.exists(pptx_path):
# #         return []
# #         
# #     prs = Presentation(pptx_path)
# #     footer_cleanup_needed = False
# #     for slide in prs.slides:
# #         before = [shape.text_frame.text for shape in slide.shapes if getattr(shape, "has_text_frame", False) and _is_footer_like_placeholder(shape)]
# #         _clear_corrupt_footer_placeholders(slide)
# #         after = [shape.text_frame.text for shape in slide.shapes if getattr(shape, "has_text_frame", False) and _is_footer_like_placeholder(shape)]
# #         if before != after:
# #             footer_cleanup_needed = True
# #     if footer_cleanup_needed:
# #         prs.save(pptx_path)
# # 
# #     prs_w = prs.slide_width
# #     prs_h = prs.slide_height
# #     
# #     try:
# #         if hasattr(fitz, "TOOLS"):
# #             fitz.TOOLS.mupdf_display_errors(False)
# #     except Exception:
# #         pass
# # 
# #     soffice_path = _get_libreoffice_path()
# #     pdf_doc = None
# #     temp_dir = None
# # 
# #     # View-mode previews use local LibreOffice directly. ONLYOFFICE is reserved
# #     # for the explicit manual-editor iframe because remote conversion is slow.
# #     if soffice_path and pdf_doc is None:
# #         try:
# #             # Create a project-local temp directory to ensure absolute permission matches
# #             temp_dir = os.path.abspath(f"data/ppt/tmp_render_{session_id}")
# #             os.makedirs(temp_dir, exist_ok=True)
# #             
# #             # Run LibreOffice conversion
# #             # Ensure profile path is absolute and uses file:/// URI scheme for headless run
# #             profile_path = os.path.join(temp_dir, "profile")
# #             profile_url = f"file://{profile_path}"
# #             if not profile_url.startswith("file:///"):
# #                 profile_url = profile_url.replace("file://", "file:///")
# #                 
# #             cmd = [
# #                 soffice_path,
# #                 "--headless",
# #                 f"-env:UserInstallation={profile_url}",
# #                 "--convert-to",
# #                 "pdf",
# #                 "--outdir",
# #                 temp_dir,
# #                 os.path.abspath(pptx_path),
# #             ]
# #             subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15.0)
# # 
# #             pdf_filename = f"{session_id}.pdf"
# #             pdf_path = os.path.join(temp_dir, pdf_filename)
# #             if os.path.exists(pdf_path):
# #                 pdf_doc = fitz.open(pdf_path)
# #         except Exception as lo_err:
# #             logger.error(f"LibreOffice PDF conversion failed in re-render: {lo_err}")
# # 
# #     store_list = []
# #     for i, slide in enumerate(prs.slides):
# #         meta = _extract_slide_meta(slide, i, prs_w, prs_h, session_id=session_id)
# #         img_b64 = ""
# # 
# #         if pdf_doc and i < len(pdf_doc):
# #             try:
# #                 page = pdf_doc[i]
# #                 pix = page.get_pixmap(dpi=120)
# #                 png_bytes = pix.tobytes("png")
# #                 img_b64 = base64.b64encode(png_bytes).decode()
# #             except Exception as page_err:
# #                 logger.error(f"Failed to render slide page {i} from PDF: {page_err}")
# # 
# #         if not img_b64:
# #             img_b64 = _slide_to_png_b64(slide, prs_w, prs_h)
# # 
# #         # Merge images (prefer physical slide extracted images if present)
# #         slide_images = meta.get("images", [])
# #         if not slide_images and i < len(old_slides):
# #             slide_images = old_slides[i].get("images", [])
# # 
# #         store_list.append({**meta, "img_b64": img_b64, "images": slide_images})
# # 
# #     if pdf_doc:
# #         pdf_doc.close()
# #     if temp_dir and os.path.exists(temp_dir):
# #         try:
# #             shutil.rmtree(temp_dir, ignore_errors=True)
# #         except Exception:
# #             pass
# # 
# #     _slide_store[session_id] = store_list
# #     set_latest_upload_sid(session_id)
# #     return store_list
# # 
# # 
# # @router.post("/add_slide")
# # async def add_slide(cmd: AddSlideCmd):
# #     """Appends a slide manually or programmatically, saves & renders, and emits reload."""
# #     from backend.queues.bus import bus
# # 
# #     session_id = resolve_sid(cmd.session_id)
# #     slides = list(_slide_store.get(session_id, []))
# # 
# #     # Guard against session_id mismatch
# #     if not slides:
# #         latest_sid = get_latest_upload_sid()
# #         if latest_sid and _slide_store.get(latest_sid):
# #             session_id = latest_sid
# #             slides = list(_slide_store.get(session_id, []))
# # 
# #     pptx_path = f"data/ppt/{session_id}.pptx"
# #     
# #     title_text = cmd.title or "New Slide"
# #     bullets = cmd.bullets or ["Write content or use voice to edit this slide"]
# #     notes_text = cmd.notes or ""
# # 
# #     if os.path.exists(pptx_path):
# #         try:
# #             _add_slide_in_pptx(pptx_path, title_text, bullets, notes_text)
# #         except Exception as e:
# #             logger.error(f"Failed to add slide to physical presentation: {e}")
# #             slides_to_save = []
# #             for s in slides:
# #                 slides_to_save.append({
# #                     "title": s.get("title", ""),
# #                     "bullets": s.get("bullets", []),
# #                     "notes": s.get("notes", ""),
# #                     "images": s.get("images", [])
# #                 })
# #             slides_to_save.append({"title": title_text, "bullets": bullets, "notes": notes_text, "images": []})
# #             save_and_render_pptx(slides_to_save, session_id, topic="Presentation")
# #     else:
# #         slides_to_save = []
# #         for s in slides:
# #             slides_to_save.append({
# #                 "title": s.get("title", ""),
# #                 "bullets": s.get("bullets", []),
# #                 "notes": s.get("notes", ""),
# #                 "images": s.get("images", [])
# #             })
# #         slides_to_save.append({"title": title_text, "bullets": bullets, "notes": notes_text, "images": []})
# #         save_and_render_pptx(slides_to_save, session_id, topic="Presentation")
# # 
# #     slides.append({"title": title_text, "bullets": bullets, "notes": notes_text, "images": []})
# #     new_idx = len(slides) - 1
# # 
# #     try:
# #         rendered = _re_render_pptx(session_id, slides)
# #         _current_slide[session_id] = new_idx
# # 
# #         # Emit a reload event and navigate to the new slide
# #         await bus.emit_event(
# #             "ppt_command",
# #             {
# #                 "action": "reload",
# #                 "slides": rendered,
# #                 "filename": f"Presentation ({len(rendered)} slides)",
# #                 "index": new_idx,
# #                 "preserveCurrent": False,
# #             },
# #             session_id,
# #         )
# # 
# #         return {"status": "ok", "slide_count": len(rendered), "slides": rendered, "index": new_idx}
# #     except Exception as e:
# #         logger.error(f"Error adding slide: {e}")
# #         raise HTTPException(status_code=500, detail=str(e))
# # @router.post("/delete_slide")
# # async def delete_slide(cmd: DeleteSlideCmd):
# #     """Deletes a slide by index, saves & renders, and emits reload."""
# #     from backend.queues.bus import bus
# # 
# #     session_id = resolve_sid(cmd.session_id)
# #     slides = list(_slide_store.get(session_id, []))
# #     if not slides:
# #         raise HTTPException(status_code=400, detail="No active presentation to delete slide from.")
# # 
# #     if cmd.slide_index < 0 or cmd.slide_index >= len(slides):
# #         raise HTTPException(
# #             status_code=400, detail=f"Slide index {cmd.slide_index} out of range (0-{len(slides) - 1})"
# #         )
# # 
# #     # 1. Modify physical presentation file on disk if exists
# #     pptx_path = f"data/ppt/{session_id}.pptx"
# #     if os.path.exists(pptx_path):
# #         try:
# #             _delete_slide_in_pptx(pptx_path, cmd.slide_index)
# #         except Exception as e:
# #             logger.error(f"Failed to delete slide from physical presentation: {e}")
# # 
# #     # Remove slide from memory cache
# #     slides.pop(cmd.slide_index)
# # 
# #     try:
# #         # Re-render using our helper function
# #         rendered = _re_render_pptx(session_id, slides)
# #         new_idx = min(cmd.slide_index, max(len(rendered) - 1, 0))
# #         _current_slide[session_id] = new_idx
# # 
# #         # Emit a reload event to update the frontend state in real-time
# #         await bus.emit_event(
# #             "ppt_command",
# #             {
# #                 "action": "reload",
# #                 "slides": rendered,
# #                 "filename": f"Presentation ({len(rendered)} slides)",
# #                 "index": new_idx,
# #                 "preserveCurrent": False,
# #             },
# #             session_id,
# #         )
# # 
# #         return {"status": "ok", "slide_count": len(rendered), "slides": rendered, "index": new_idx}
# #     except Exception as e:
# #         logger.error(f"Error deleting slide: {e}")
# #         raise HTTPException(status_code=500, detail=str(e))
# # @router.post("/edit_slide")
# # async def edit_slide(cmd: EditSlideCmd):
# #     """Edits a slide by index, saves & renders, and emits reload preserving current index."""
# #     from backend.queues.bus import bus
# # 
# #     session_id = resolve_sid(cmd.session_id)
# #     slides = list(_slide_store.get(session_id, []))
# #     if not slides:
# #         raise HTTPException(status_code=400, detail="No active presentation to edit.")
# # 
# #     idx = cmd.slide_index
# #     if idx < 0 or idx >= len(slides):
# #         raise HTTPException(status_code=400, detail=f"Slide index {idx} out of range (0-{len(slides) - 1})")
# # 
# #     # Apply modifications
# #     if cmd.title is not None:
# #         slides[idx]["title"] = cmd.title
# #     if cmd.bullets is not None:
# #         slides[idx]["bullets"] = cmd.bullets
# #     elif cmd.add_bullet is not None:
# #         if "bullets" not in slides[idx] or not isinstance(slides[idx]["bullets"], list):
# #             slides[idx]["bullets"] = []
# #         slides[idx]["bullets"].append(cmd.add_bullet)
# #     if cmd.notes is not None:
# #         slides[idx]["notes"] = cmd.notes
# #     if cmd.images is not None:
# #         slides[idx]["images"] = cmd.images
# # 
# #     # 1. Update the physical presentation on disk if it exists
# #     pptx_path = f"data/ppt/{session_id}.pptx"
# #     if os.path.exists(pptx_path):
# #         try:
# #             _update_slide_in_pptx(
# #                 pptx_path,
# #                 idx,
# #                 title=cmd.title,
# #                 bullets=slides[idx].get("bullets"),
# #                 notes=cmd.notes,
# #                 images=slides[idx].get("images")
# #             )
# #         except Exception as e:
# #             logger.error(f"Failed to update physical slide: {e}")
# #             slides_to_save = []
# #             for s in slides:
# #                 slides_to_save.append({
# #                     "title": s.get("title", ""),
# #                     "bullets": s.get("bullets", []),
# #                     "notes": s.get("notes", ""),
# #                     "images": s.get("images", [])
# #                 })
# #             save_and_render_pptx(slides_to_save, session_id, topic="Presentation")
# #     else:
# #         slides_to_save = []
# #         for s in slides:
# #             slides_to_save.append({
# #                 "title": s.get("title", ""),
# #                 "bullets": s.get("bullets", []),
# #                 "notes": s.get("notes", ""),
# #                 "images": s.get("images", [])
# #             })
# #         save_and_render_pptx(slides_to_save, session_id, topic="Presentation")
# # 
# #     try:
# #         # Re-render using our helper function
# #         rendered = _re_render_pptx(session_id, slides)
# #         _current_slide[session_id] = idx
# # 
# #         # Emit a reload event preserving current slide view
# #         await bus.emit_event(
# #             "ppt_command",
# #             {
# #                 "action": "reload",
# #                 "slides": rendered,
# #                 "filename": f"Presentation ({len(rendered)} slides)",
# #                 "index": idx,
# #                 "preserveCurrent": True,
# #             },
# #             session_id,
# #         )
# # 
# #         return {"status": "ok", "slide_count": len(rendered), "slides": rendered, "index": idx}
# #     except Exception as e:
# #         logger.error(f"Error editing slide: {e}")
# #         raise HTTPException(status_code=500, detail=str(e))
# # @router.post("/clear")
# # async def clear_presentation_endpoint(cmd: ClearCmd):
# #     """Clears the presentation and starts with 1 blank title slide."""
# #     from backend.queues.bus import bus
# # 
# #     session_id = cmd.session_id
# #     default_slides = [
# #         {
# #             "title": "New Presentation",
# #             "bullets": ["Voice-Driven Interactive Presentation", "Start speaking or clicking to add slides"],
# #             "notes": "Welcome to your new interactive presentation deck.",
# #             "images": [],
# #         }
# #     ]
# # 
# #     try:
# #         rendered = save_and_render_pptx(default_slides, session_id, topic="New Presentation")
# #         _current_slide[session_id] = 0
# # 
# #         await bus.emit_event(
# #             "ppt_command",
# #             {
# #                 "action": "reload",
# #                 "slides": rendered,
# #                 "filename": "New Presentation",
# #                 "index": 0,
# #                 "preserveCurrent": False,
# #             },
# #             session_id,
# #         )
# # 
# #         return {"status": "ok", "slide_count": len(rendered), "slides": rendered, "index": 0}
# #     except Exception as e:
# #         logger.error(f"Error clearing presentation: {e}")
# #         raise HTTPException(status_code=500, detail=str(e))
# # 
# # 
# # @router.post("/upload_image")
# # async def upload_image(session_id: str, slide_index: int, file: UploadFile = File(...)):
# #     """Uploads an image for a specific slide, saves it, inserts it into the slide design, and re-renders."""
# #     from backend.queues.bus import bus
# # 
# #     session_id = resolve_sid(session_id)
# #     slides = list(_slide_store.get(session_id, []))
# #     if not slides:
# #         raise HTTPException(status_code=400, detail="No active presentation to add image to.")
# # 
# #     if slide_index < 0 or slide_index >= len(slides):
# #         raise HTTPException(
# #             status_code=400, detail=f"Slide index {slide_index} out of range (0-{len(slides) - 1})"
# #         )
# # 
# #     # Ensure images directory exists
# #     img_dir = "data/ppt/images"
# #     os.makedirs(img_dir, exist_ok=True)
# # 
# #     # Save the file with a clean unique name
# #     ext = os.path.splitext(file.filename)[1] or ".png"
# #     filename = f"{session_id}_{slide_index}_{int(time.time())}{ext}"
# #     saved_path = os.path.join(img_dir, filename)
# # 
# #     try:
# #         content = await file.read()
# #         with open(saved_path, "wb") as f:
# #             f.write(content)
# # 
# #         # Add to slide metadata (clear previous images for a clean layout)
# #         slides[slide_index]["images"] = [
# #             {"path": saved_path, "left_in": 7.5, "top_in": 1.8, "width_in": 5.0, "height_in": 4.5}
# #         ]
# # 
# #         # Update the physical presentation on disk if it exists to preserve layout/theme
# #         pptx_path = f"data/ppt/{session_id}.pptx"
# #         if os.path.exists(pptx_path):
# #             try:
# #                 _update_slide_in_pptx(
# #                     pptx_path,
# #                     slide_index,
# #                     title=None,
# #                     bullets=None,
# #                     notes=None,
# #                     images=slides[slide_index]["images"]
# #                 )
# #                 rendered = _re_render_pptx(session_id, slides)
# #             except Exception as e:
# #                 logger.error(f"Failed to update slide in-place on image upload: {e}")
# #                 slides_to_save = []
# #                 for s in slides:
# #                     slides_to_save.append({
# #                         "title": s.get("title", ""),
# #                         "bullets": s.get("bullets", []),
# #                         "notes": s.get("notes", ""),
# #                         "images": s.get("images", [])
# #                     })
# #                 rendered = save_and_render_pptx(slides_to_save, session_id, topic="Presentation")
# #         else:
# #             slides_to_save = []
# #             for s in slides:
# #                 slides_to_save.append({
# #                     "title": s.get("title", ""),
# #                     "bullets": s.get("bullets", []),
# #                     "notes": s.get("notes", ""),
# #                     "images": s.get("images", [])
# #                 })
# #             rendered = save_and_render_pptx(slides_to_save, session_id, topic="Presentation")
# #         _current_slide[session_id] = slide_index
# # 
# #         # Emit a reload event preserving current slide view
# #         await bus.emit_event(
# #             "ppt_command",
# #             {
# #                 "action": "reload",
# #                 "slides": rendered,
# #                 "filename": f"Presentation ({len(rendered)} slides)",
# #                 "index": slide_index,
# #                 "preserveCurrent": True,
# #             },
# #             session_id,
# #         )
# # 
# #         return {"status": "ok", "slide_count": len(rendered), "slides": rendered, "index": slide_index}
# #     except Exception as e:
# #         logger.error(f"Error uploading image to slide: {e}")
# #         raise HTTPException(status_code=500, detail=str(e))
# # 
# # 
# # @router.post("/improvise_slide")
# # async def improvise_slide(cmd: ImproviseSlideCmd):
# #     """Uses LLM to improvise the content of a single slide based on user instructions, then saves and renders."""
# #     from backend.queues.bus import bus
# # 
# #     session_id = resolve_sid(cmd.session_id)
# #     slides = list(_slide_store.get(session_id, []))
# #     if not slides:
# #         raise HTTPException(status_code=400, detail="No active presentation to improvise.")
# # 
# #     idx = cmd.slide_index
# #     if idx < 0 or idx >= len(slides):
# #         raise HTTPException(status_code=400, detail=f"Slide index {idx} out of range (0-{len(slides) - 1})")
# # 
# #     current_slide = slides[idx]
# # 
# #     try:
# #         # Call LLM to improve slide
# #         improved = await improvise_slide_content(current_slide, cmd.prompt)
# # 
# #         # Update text fields but preserve any images!
# #         slides[idx]["title"] = improved["title"]
# #         slides[idx]["bullets"] = improved["bullets"]
# #         slides[idx]["notes"] = improved["notes"]
# # 
# #         # 1. Update the physical presentation on disk if it exists
# #         pptx_path = f"data/ppt/{session_id}.pptx"
# #         if os.path.exists(pptx_path):
# #             try:
# #                 _update_slide_in_pptx(
# #                     pptx_path,
# #                     idx,
# #                     title=slides[idx]["title"],
# #                     bullets=slides[idx]["bullets"],
# #                     notes=slides[idx]["notes"]
# #                 )
# #             except Exception as e:
# #                 logger.error(f"Failed to update physical slide during improvise: {e}")
# #                 slides_to_save = []
# #                 for s in slides:
# #                     slides_to_save.append({
# #                         "title": s.get("title", ""),
# #                         "bullets": s.get("bullets", []),
# #                         "notes": s.get("notes", ""),
# #                         "images": s.get("images", [])
# #                     })
# #                 save_and_render_pptx(slides_to_save, session_id, topic="Presentation")
# #         else:
# #             slides_to_save = []
# #             for s in slides:
# #                 slides_to_save.append({
# #                     "title": s.get("title", ""),
# #                     "bullets": s.get("bullets", []),
# #                     "notes": s.get("notes", ""),
# #                     "images": s.get("images", [])
# #                 })
# #             save_and_render_pptx(slides_to_save, session_id, topic="Presentation")
# # 
# #         # Re-render using our helper function
# #         rendered = _re_render_pptx(session_id, slides)
# #         _current_slide[session_id] = idx
# # 
# #         # Emit a reload event preserving current slide view
# #         await bus.emit_event(
# #             "ppt_command",
# #             {
# #                 "action": "reload",
# #                 "slides": rendered,
# #                 "filename": f"Presentation ({len(rendered)} slides)",
# #                 "index": idx,
# #                 "preserveCurrent": True,
# #             },
# #             session_id,
# #         )
# # 
# #         return {"status": "ok", "slide_count": len(rendered), "slides": rendered, "index": idx}
# #     except Exception as e:
# #         logger.error(f"Error improvising slide: {e}")
# #         raise HTTPException(status_code=500, detail=str(e))
# # @router.post("/create")
# # async def create_presentation(cmd: CreateCmd):
# #     """Generates a presentation dynamically from a text prompt using Ollama qwen2.5:7b, then renders it."""
# #     from backend.queues.bus import bus
# # 
# #     try:
# #         slides = await create_presentation_from_prompt(
# #             prompt=cmd.prompt, session_id=cmd.session_id, slide_count=cmd.slide_count
# #         )
# #         # Emit a reload event to update the frontend state in real-time
# #         await bus.emit_event(
# #             "ppt_command",
# #             {"action": "reload", "slides": slides, "filename": f"AI: {cmd.prompt}"},
# #             cmd.session_id,
# #         )
# # 
# #         return {"status": "ok", "slide_count": len(slides), "slides": slides}
# #     except Exception as e:
# #         logger.error(f"Error generating presentation: {e}")
# #         raise HTTPException(status_code=500, detail=str(e))
# # 
# # 
# # @router.get("/download/{session_id}")
# # async def download_presentation(session_id: str):
# #     """Exposes the session's physical .pptx file for conversion services (like OnlyOffice)."""
# #     pptx_path = f"data/ppt/{session_id}.pptx"
# #     if not os.path.exists(pptx_path):
# #         raise HTTPException(status_code=404, detail="File not found")
# #     
# #     from fastapi.responses import FileResponse
# #     return FileResponse(pptx_path)
# # 
# # 
# # @router.post("/callback/{session_id}")
# # async def onlyoffice_callback(session_id: str, request: Request):
# #     """
# #     Callback endpoint called by OnlyOffice Document Server when edits are saved.
# #     Downloads the updated PPTX from OnlyOffice, overwrites local copy, and re-renders.
# #     """
# #     from backend.queues.bus import bus
# #     import httpx
# #     
# #     try:
# #         body = await request.json()
# #         logger.info(f"Received OnlyOffice callback for session {session_id}: status={body.get('status')}")
# #         
# #         status = body.get("status")
# #         # 2 = document ready for saving, 6 = document is being force-saved
# #         if status in (2, 6):
# #             download_url = body.get("url")
# #             if download_url:
# #                 # Download file from OnlyOffice
# #                 async with httpx.AsyncClient(timeout=30.0) as client:
# #                     resp = await client.get(download_url)
# #                     if resp.status_code == 200:
# #                         pptx_path = f"data/ppt/{session_id}.pptx"
# #                         os.makedirs("data/ppt", exist_ok=True)
# #                         with open(pptx_path, "wb") as f_out:
# #                             f_out.write(resp.content)
# #                         
# #                         # Re-render the presentation slides from the new PPTX
# #                         old_slides = list(_slide_store.get(session_id, []))
# #                         rendered = _re_render_pptx(session_id, old_slides)
# #                         _slide_store[session_id] = rendered
# #                         
# #                         # Emit a reload event preserving current slide view
# #                         await bus.emit_event(
# #                             "ppt_command",
# #                             {
# #                                 "action": "reload",
# #                                 "slides": rendered,
# #                                 "filename": f"Presentation ({len(rendered)} slides)",
# #                                 "index": _current_slide.get(session_id, 0),
# #                                 "preserveCurrent": True,
# #                             },
# #                             session_id,
# #                         )
# #                         logger.info(f"Successfully processed OnlyOffice save callback and updated session {session_id}")
# #                     else:
# #                         logger.error(f"Failed to download edited presentation from OnlyOffice: HTTP {resp.status_code}")
# #                         
# #         return {"error": 0}
# #     except Exception as e:
# #         logger.error(f"Error handling OnlyOffice callback for session {session_id}: {e}")
# #         return {"error": 0}
# # 
# # 
# # @router.get("/config")
# # async def get_ppt_config():
# #     """Returns PPT configuration status, including ONLYOFFICE Document Server settings."""
# #     from backend.core.config import settings
# #     public_onlyoffice_url = settings.ONLYOFFICE_PUBLIC_URL or settings.ONLYOFFICE_URL
# #     return {
# #         "onlyoffice_url": public_onlyoffice_url,
# #         "onlyoffice_internal_url": settings.ONLYOFFICE_URL,
# #         "app_url": settings.APP_URL
# #     }
# # 
# # 
# # @router.get("/editor-config/{session_id}")
# # async def get_editor_config(session_id: str):
# #     """
# #     Returns a signed editor configuration for OnlyOffice Document Editor.
# #     Uses settings.APP_URL (or host.docker.internal fallback) for container-reachability.
# #     """
# #     from backend.core.config import settings
# #     import jwt
# #     import os
# #     import time
# #     
# #     pptx_path = f"data/ppt/{session_id}.pptx"
# #     if not os.path.exists(pptx_path):
# #         mtime = time.time_ns()
# #     else:
# #         mtime = os.stat(pptx_path).st_mtime_ns
# #         
# #     base_url = settings.APP_URL or "http://host.docker.internal:8000"
# #     doc_url = f"{base_url}/api/v1/ppt/download/{session_id}"
# #     cb_url = f"{base_url}/api/v1/ppt/callback/{session_id}"
# #     
# #     config = {
# #         "document": {
# #             "fileType": "pptx",
# #             "key": f"{session_id}_{mtime}",
# #             "title": f"Presentation_{session_id}.pptx",
# #             "url": doc_url
# #         },
# #         "documentType": "slide",
# #         "editorConfig": {
# #             "callbackUrl": cb_url,
# #             "customization": {
# #                 "forcesave": True,
# #                 "goback": False
# #             }
# #         },
# #         "height": "100%",
# #         "width": "100%"
# #     }
# #     
# #     if settings.ONLYOFFICE_JWT_SECRET:
# #         token = jwt.encode(config, settings.ONLYOFFICE_JWT_SECRET, algorithm="HS256")
# #         config["token"] = token
# #         
# #     return config
# 
# 
# # ============================================================================
# # ACTIVE IMPLEMENTATION — GD-template PPT generation, kind-aware editing,
# # add-slide. Migrated from syugesh/pilot-voice-agent-backend
# # (feature/ppt-copilot @ 2d3df73cc8742a40e0760d83589a29f267c050d4).
# # Travel-planner and system-guidelines changes from that commit were
# # excluded per project scope.
# # ============================================================================
# """PPT API — navigate + file upload + AI generation with slide extraction."""
# from fastapi import APIRouter, UploadFile, File, HTTPException
# from fastapi.responses import FileResponse
# from pydantic import BaseModel
# from typing import List, Optional
# import os, json, asyncio, subprocess, shutil, glob, logging, time, re, tempfile, json
# 
# logger = logging.getLogger("pilot.ppt")
# 
# router = APIRouter()
# 
# class PPTCmd(BaseModel):
#     session_id: str
#     direction:  str
#     slide_index: int = -1
# 
# class JumpCmd(BaseModel):
#     session_id: str
#     query:      str
# 
# # _slide_store / _current_slide / _latest_upload_sid / resolve_sid / etc. are
# # NOT redeclared here — they're already imported at module load (see the
# # `from backend.core.slide_store import (...)` block near the top of this
# # file, kept active from the legacy header) so this module shares live
# # session state with the PILOT-native ppt_copilot.py tools (ppt_qa,
# # ppt_clear_presentation, ppt_improvise_slide, ppt_create_slides,
# # ppt_save_slide, route_page) that read/write that same store — otherwise a
# # deck uploaded/generated here would be invisible to those tools.
# _ppt_titles:  dict[str, str]       = {}   # session_id → presentation title (for download filename)
# _slide_version_store: dict[str, dict[int, list[dict]]] = {}  # session_id → slide_index → previous versions
# 
# _INDEX_PATH = "data/ppt/index.json"
# 
# 
# def _load_index() -> list[dict]:
#     try:
#         if os.path.exists(_INDEX_PATH):
#             with open(_INDEX_PATH) as f:
#                 return json.load(f)
#     except Exception:
#         pass
#     return []
# 
# 
# def _kinds_path(session_id: str) -> str:
#     return f"data/ppt/{session_id}.kinds.json"
# 
# 
# def _save_kinds(session_id: str, kinds: list):
#     """Persist the per-slide template 'kind' list (e.g. "team", "table", or
#     None for slides with no known kind) alongside a generated deck. A .pptx
#     file has no field for "this slide is a team slide" — this sidecar is
#     what lets editing be kind-aware after the fact, since _slide_store is
#     rebuilt from scratch by re-reading the file on every load."""
#     os.makedirs("data/ppt", exist_ok=True)
#     with open(_kinds_path(session_id), "w") as f:
#         json.dump(kinds, f)
# 
# 
# def _load_kinds(session_id: str) -> list | None:
#     try:
#         path = _kinds_path(session_id)
#         if os.path.exists(path):
#             with open(path) as f:
#                 return json.load(f)
#     except Exception:
#         pass
#     return None
# 
# 
# def _save_index_entry(session_id: str, title: str, description: str, slide_count: int):
#     os.makedirs("data/ppt", exist_ok=True)
#     entries = _load_index()
#     # Remove any prior entry for this session (re-generation)
#     entries = [e for e in entries if e.get("session_id") != session_id]
#     entries.insert(0, {
#         "session_id":  session_id,
#         "title":       title,
#         "description": description,
#         "slide_count": slide_count,
#         "created_at":  __import__("datetime").datetime.now().isoformat(timespec="seconds"),
#     })
#     with open(_INDEX_PATH, "w") as f:
#         json.dump(entries[:50], f, indent=2)   # keep last 50
# 
# 
# @router.post("/navigate")
# async def navigate(cmd: PPTCmd):
#     from backend.tools.ppt_copilot import ppt_navigate
#     return await ppt_navigate({"direction": cmd.direction}, cmd.session_id)
# 
# 
# @router.post("/jump")
# async def jump(cmd: JumpCmd):
#     """Fuzzy match slide title → navigate to it."""
#     from backend.queues.bus import bus
#     slides = _slide_store.get(cmd.session_id, [])
#     q = cmd.query.lower()
#     best_idx = -1
#     best_score = 0
#     for s in slides:
#         title = s.get("title","").lower()
#         score = sum(1 for word in q.split() if word in title)
#         # Also match slide number directly: "slide 42" → index 41
#         import re
#         m = re.search(r'\b(\d+)\b', q)
#         if m:
#             num = int(m.group(1)) - 1  # 0-indexed
#             if 0 <= num < len(slides):
#                 best_idx = num
#                 best_score = 99
#                 break
#         if score > best_score:
#             best_score = score
#             best_idx = s.get("index", -1)
# 
#     if best_idx >= 0:
#         await bus.emit_event("ppt_command", {"action": "goto", "index": best_idx}, cmd.session_id)
#         title = slides[best_idx]["title"] if best_idx < len(slides) else f"Slide {best_idx+1}"
#         return {"status": "ok", "index": best_idx, "title": title}
#     return {"status": "not_found"}
# 
# 
# def _find_soffice() -> str | None:
#     """Find LibreOffice executable on macOS or Linux."""
#     candidates = [
#         "/Applications/LibreOffice.app/Contents/MacOS/soffice",
#         shutil.which("libreoffice"),
#         shutil.which("soffice"),
#     ]
#     for c in candidates:
#         if c and os.path.exists(c):
#             return c
#     return None
# 
# 
# def _find_pdftoppm() -> str | None:
#     candidates = [
#         "/opt/homebrew/bin/pdftoppm",
#         shutil.which("pdftoppm"),
#     ]
#     for c in candidates:
#         if c and os.path.exists(c):
#             return c
#     return None
# 
# 
# def _convert_to_images_sync(pptx_path: str, out_dir: str) -> list[str]:
#     """Convert every slide to a PNG: PPTX → PDF (LibreOffice) → PNGs (pdftoppm)."""
#     soffice = _find_soffice()
#     if not soffice:
#         return []
#     os.makedirs(out_dir, exist_ok=True)
# 
#     # Clear stale output from a previous upload into this same slot — otherwise
#     # leftover slide-N.png from a bigger deck lingers on disk after a smaller
#     # deck is uploaded in its place.
#     for stale in glob.glob(os.path.join(out_dir, "slide-*.png")) + glob.glob(os.path.join(out_dir, "*.pdf")):
#         try:
#             os.remove(stale)
#         except OSError:
#             pass
# 
#     # Step 1: PPTX → PDF — LibreOffice produces a faithful multi-page PDF
#     r = subprocess.run(
#         [soffice, "--headless", "--convert-to", "pdf", "--outdir", out_dir, pptx_path],
#         capture_output=True, text=True, timeout=120,
#     )
#     basename = os.path.splitext(os.path.basename(pptx_path))[0]
#     pdf_path = os.path.join(out_dir, f"{basename}.pdf")
#     if r.returncode != 0 or not os.path.exists(pdf_path):
#         logger.error(f"LibreOffice PDF conversion failed: {r.stderr[:300]}")
#         return []
# 
#     # Step 2: PDF pages → PNGs via pdftoppm (installed via: brew install poppler)
#     pdftoppm = _find_pdftoppm()
#     if not pdftoppm:
#         logger.warning("pdftoppm not found — install poppler: brew install poppler")
#         return []
# 
#     prefix = os.path.join(out_dir, "slide")
#     r2 = subprocess.run(
#         [pdftoppm, "-png", "-r", "150", pdf_path, prefix],
#         capture_output=True, text=True, timeout=120,
#     )
#     if r2.returncode != 0:
#         logger.error(f"pdftoppm failed: {r2.stderr[:300]}")
#         return []
# 
#     # pdftoppm outputs: slide-1.png, slide-2.png … (or slide-01.png with zero-padding)
#     images = sorted(glob.glob(os.path.join(out_dir, "slide-*.png")),
#                     key=lambda p: int("".join(filter(str.isdigit, os.path.basename(p))) or "0"))
#     logger.info(f"Converted {len(images)} slides to PNG")
#     return images
# 
# 
# @router.post("/upload")
# async def upload_ppt(session_id: str, file: UploadFile = File(...)):
#     """Accept .pptx, convert slides to images (LibreOffice) + extract text metadata."""
#     if not file.filename.endswith((".pptx", ".ppt")):
#         raise HTTPException(400, "Only .pptx files supported")
#     content = await file.read()
#     os.makedirs("data/ppt", exist_ok=True)
#     path = f"data/ppt/{session_id}.pptx"
#     with open(path, "wb") as f:
#         f.write(content)
# 
#     # Convert to slide images (faithful visual) — run in thread (blocking)
#     img_dir   = f"data/ppt/slides/{session_id}"
#     img_paths = await asyncio.to_thread(_convert_to_images_sync, path, img_dir)
# 
#     # Extract text metadata for voice navigation + summarise
#     slides = await _extract_slides(path)
# 
#     # Attach image URL to each slide if conversion succeeded. The version query
#     # param forces the browser to refetch even when session_id + filename are
#     # identical to a previous upload (e.g. repeated uploads into the same
#     # session, or the "default" slot used before a live session exists) —
#     # without it the <img>  src string never changes and the browser keeps
#     # showing the previous deck's already-decoded image.
#     version = int(time.time() * 1000)
#     for i, slide in enumerate(slides):
#         if i < len(img_paths):
#             fname = os.path.basename(img_paths[i])
#             slide["image_url"] = f"/api/v1/ppt/image/{session_id}/{fname}?v={version}"
# 
#     _slide_store[session_id] = slides
#     set_latest_upload_sid(session_id)
#     _current_slide[session_id] = 0
#     return {"status": "ok", "slide_count": len(slides), "slides": slides}
# 
# 
# @router.get("/image/{session_id}/{filename}")
# async def serve_slide_image(session_id: str, filename: str):
#     """Serve a converted slide PNG."""
#     # Basic path-traversal guard
#     if ".." in filename or "/" in filename:
#         raise HTTPException(400, "Invalid filename")
#     path = f"data/ppt/slides/{session_id}/{filename}"
#     if not os.path.exists(path):
#         raise HTTPException(404, "Slide image not found")
#     return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})
# 
# 
# @router.get("/slides/{session_id}")
# async def get_slides(session_id: str):
#     return {"slides": _slide_store.get(session_id, [])}
# 
# 
# class GenerateRequest(BaseModel):
#     session_id:  str
#     description: str
#     slide_count: int = 10
# 
# 
# @router.post("/generate")
# async def generate_ppt(req: GenerateRequest):
#     """
#     AI-generate a .pptx from a text description.
#     Uses Ollama (local, free) to write kind-tagged slide content, then clones
#     matching slides out of the real Grid Dynamics template and swaps in that
#     content — see services/ppt_template_builder.py.
#     Returns the same slide format as /upload so the viewer loads immediately.
#     """
#     if not req.description.strip():
#         raise HTTPException(400, "Description cannot be empty")
#     slide_count = max(3, min(req.slide_count, 20))
# 
#     # Step 1 — generate kind-tagged slide content with Ollama
#     from backend.services.ppt_template_builder import generate_template_content, build_deck_from_template
#     content = await generate_template_content(req.description, slide_count)
#     if not content:
#         raise HTTPException(502, "Ollama content generation failed — is Ollama running?")
# 
#     # Step 2 — clone the matching GD template slides and populate them
#     os.makedirs("data/ppt", exist_ok=True)
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     await asyncio.to_thread(build_deck_from_template, content, pptx_path)
#     _save_kinds(req.session_id, [s.get("kind") for s in content["slides"]])
# 
#     # Step 3 — convert to slide images if LibreOffice is available (same as upload)
#     img_dir   = f"data/ppt/slides/{req.session_id}"
#     img_paths = await asyncio.to_thread(_convert_to_images_sync, pptx_path, img_dir)
# 
#     # Step 4 — extract metadata into the same format the viewer expects
#     slides = await _extract_slides(pptx_path)
#     version = int(time.time() * 1000)
#     for i, slide in enumerate(slides):
#         if i < len(img_paths):
#             fname = os.path.basename(img_paths[i])
#             slide["image_url"] = f"/api/v1/ppt/image/{req.session_id}/{fname}?v={version}"
# 
#     _slide_store[req.session_id] = slides
#     set_latest_upload_sid(req.session_id)
#     _current_slide[req.session_id] = 0
# 
#     title = content.get("presentation_title", "Generated Presentation")
#     _ppt_titles[req.session_id] = title
#     _save_index_entry(req.session_id, title, req.description, len(slides))
#     logger.info(f"Generated '{title}' — {len(slides)} slides for session {req.session_id[:8]}")
#     return {"status": "ok", "slide_count": len(slides), "slides": slides, "title": title}
# 
# 
# @router.get("/download/{session_id}")
# async def download_ppt(session_id: str):
#     """Download the .pptx file for this session (works for both uploaded and generated files)."""
#     # Basic path-traversal guard
#     if re.search(r'[/\\.]\.', session_id):
#         raise HTTPException(400, "Invalid session id")
#     path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(path):
#         raise HTTPException(404, "No presentation found for this session")
#     raw_title = _ppt_titles.get(session_id) or "presentation"
#     safe_name = re.sub(r'[^\w\s-]', '', raw_title)[:60].strip().replace(' ', '_') or "presentation"
#     return FileResponse(
#         path,
#         media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
#         filename=f"{safe_name}.pptx",
#         headers={"Content-Disposition": f'attachment; filename="{safe_name}.pptx"'},
#     )
# 
# 
# @router.get("/export-pdf/{session_id}")
# async def export_pdf(session_id: str):
#     """Export the presentation as a PDF using LibreOffice (same pipeline as slide image generation)."""
#     if re.search(r'[/\\.]\.', session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
# 
#     soffice = _find_soffice()
#     if not soffice:
#         raise HTTPException(503, "LibreOffice not installed — cannot export PDF")
# 
#     raw_title = _ppt_titles.get(session_id) or "presentation"
#     safe_name = re.sub(r'[^\w\s-]', '', raw_title)[:60].strip().replace(' ', '_') or "presentation"
# 
#     # Convert in a temp dir so concurrent exports don't collide
#     tmp_dir = tempfile.mkdtemp(prefix="pilot_pdf_")
#     try:
#         r = await asyncio.to_thread(
#             subprocess.run,
#             [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp_dir, pptx_path],
#             capture_output=True, text=True, timeout=120,
#         )
#         basename = os.path.splitext(os.path.basename(pptx_path))[0]
#         pdf_path = os.path.join(tmp_dir, f"{basename}.pdf")
#         if r.returncode != 0 or not os.path.exists(pdf_path):
#             logger.error(f"LibreOffice PDF export failed: {r.stderr[:300]}")
#             raise HTTPException(502, "PDF export failed")
#         return FileResponse(
#             pdf_path,
#             media_type="application/pdf",
#             filename=f"{safe_name}.pdf",
#             headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
#             background=None,  # keep file alive during streaming
#         )
#     except HTTPException:
#         shutil.rmtree(tmp_dir, ignore_errors=True)
#         raise
#     except Exception as e:
#         shutil.rmtree(tmp_dir, ignore_errors=True)
#         logger.error(f"PDF export error: {e}")
#         raise HTTPException(502, f"PDF export error: {e}")
# 
# 
# # ── OnlyOffice Document Server integration ──────────────────────────────────
# # Full-fidelity editing in an actual PowerPoint-like interface (an ONLYOFFICE
# # Document Server container — docker run ... onlyoffice/documentserver),
# # alongside the kind-aware slide-by-slide editor above. ONLYOFFICE fetches the
# # .pptx from /download (already served for this session), the user edits it
# # in its native UI, and it POSTs the changed file back to /callback on save.
# 
# @router.get("/config")
# async def get_ppt_config():
#     """Returns PPT configuration status, including ONLYOFFICE Document Server settings."""
#     from backend.core.config import settings
# 
#     public_onlyoffice_url = settings.ONLYOFFICE_PUBLIC_URL or settings.ONLYOFFICE_URL
#     return {
#         "onlyoffice_url": public_onlyoffice_url,
#         "onlyoffice_internal_url": settings.ONLYOFFICE_URL,
#         "app_url": settings.APP_URL,
#     }
# 
# 
# @router.get("/editor-config/{session_id}")
# async def get_editor_config(session_id: str):
#     """Returns a signed editor configuration for the ONLYOFFICE Document Editor.
#     Uses settings.APP_URL (defaults to host.docker.internal so the ONLYOFFICE
#     container — a separate process, possibly in Docker — can reach PILOT's API
#     regardless of where the browser itself is running)."""
#     import jwt as pyjwt
# 
#     from backend.core.config import settings
# 
#     if re.search(r'[/\\.]\.', session_id):
#         raise HTTPException(400, "Invalid session id")
# 
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
#     mtime = os.stat(pptx_path).st_mtime_ns
# 
#     base_url = settings.APP_URL or "http://host.docker.internal:8000"
#     raw_title = _ppt_titles.get(session_id) or "Presentation"
# 
#     config = {
#         "document": {
#             "fileType": "pptx",
#             "key": f"{session_id}_{mtime}",
#             "title": f"{raw_title}.pptx",
#             "url": f"{base_url}/api/v1/ppt/download/{session_id}",
#         },
#         "documentType": "slide",
#         "editorConfig": {
#             "callbackUrl": f"{base_url}/api/v1/ppt/callback/{session_id}",
#             "customization": {"forcesave": True, "goback": False},
#         },
#         "height": "100%",
#         "width": "100%",
#     }
# 
#     if settings.ONLYOFFICE_JWT_SECRET:
#         config["token"] = pyjwt.encode(config, settings.ONLYOFFICE_JWT_SECRET, algorithm="HS256")
# 
#     return config
# 
# 
# @router.post("/callback/{session_id}")
# async def onlyoffice_callback(session_id: str, request: Request):
#     """Callback invoked by ONLYOFFICE Document Server when the user's edits
#     are saved. Downloads the updated .pptx, overwrites the session's file,
#     re-renders thumbnails, and pushes a reload event so PPTView (and the
#     voice pipeline's slide state) pick up the change immediately."""
#     import httpx
# 
#     from backend.queues.bus import bus
# 
#     try:
#         body = await request.json()
#         logger.info(f"[ONLYOFFICE] callback for session {session_id[:8]}: status={body.get('status')}")
# 
#         status = body.get("status")
#         # 2 = document ready for saving, 6 = document is being force-saved
#         if status in (2, 6):
#             download_url = body.get("url")
#             if download_url:
#                 async with httpx.AsyncClient(timeout=30.0) as client:
#                     resp = await client.get(download_url)
#                 if resp.status_code == 200:
#                     pptx_path = f"data/ppt/{session_id}.pptx"
#                     os.makedirs("data/ppt", exist_ok=True)
#                     with open(pptx_path, "wb") as f_out:
#                         f_out.write(resp.content)
# 
#                     rendered = await refresh_slide_thumbnails_async(session_id)
#                     if rendered is not None:
#                         await bus.emit_event(
#                             "ppt_command",
#                             {
#                                 "action": "reload",
#                                 "slides": rendered,
#                                 "filename": f"Presentation ({len(rendered)} slides)",
#                                 "index": _current_slide.get(session_id, 0),
#                                 "preserveCurrent": True,
#                             },
#                             session_id,
#                         )
#                     logger.info(f"[ONLYOFFICE] Saved edits and refreshed session {session_id[:8]}")
#                 else:
#                     logger.error(f"[ONLYOFFICE] Failed to download edited file: HTTP {resp.status_code}")
# 
#         return {"error": 0}
#     except Exception as e:
#         logger.error(f"[ONLYOFFICE] callback error for session {session_id[:8]}: {e}")
#         return {"error": 0}
# 
# 
# class EditSlideReq(BaseModel):
#     session_id:  str
#     slide_index: int
#     title:       str
#     bullets:     List[str]
#     notes:       Optional[str] = ""
#     # Optional map of shape_id (as string) -> new text, for editing any other
#     # shape on the slide (subtitle, caption, footer, etc.) beyond the title
#     # and numbered-bullet body. Defaults to None = no other shapes touched.
#     other_edits: Optional[dict] = None
# 
# 
# def _extract_slide_bullets(slide: dict) -> list[str]:
#     bullets: list[str] = []
#     for sh in slide.get("shapes", []):
#         for line in sh.get("text", "").split("\n"):
#             m = re.match(r'^\d+\.\s{1,3}(.+)', line)
#             if m:
#                 bullets.append(m.group(1).strip())
#     return bullets
# 
# 
# def _remember_slide_version(session_id: str, slide_index: int, slide: dict) -> dict:
#     """Capture the editable slide fields before an AI/manual edit mutates them."""
#     version = {
#         "id": None,
#         "title": slide.get("title", ""),
#         "bullets": _extract_slide_bullets(slide),
#         "notes": slide.get("notes", ""),
#         "created_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
#     }
#     _slide_version_store.setdefault(session_id, {}).setdefault(slide_index, []).insert(0, version)
#     return version
# 
# 
# @router.patch("/slide")
# async def edit_slide(req: EditSlideReq):
#     """
#     Edit the content of a specific slide:
#       - Updates title, bullet text, and speaker notes in the .pptx on disk
#       - Re-renders slide thumbnails (full deck via LibreOffice)
#       - Updates the in-memory _slide_store so the viewer reflects changes immediately
#     """
#     if re.search(r'[/\\.]\.', req.session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
# 
#     def _writer(slide):
#         _patch_slide(slide, req.slide_index, req.title, req.bullets, req.notes or "", req.other_edits)
# 
#     slides = await apply_slide_edit_async(req.session_id, req.slide_index, _writer)
#     if slides is None:
#         raise HTTPException(400, f"Slide index {req.slide_index} out of range")
# 
#     return {"status": "ok", "slides": slides}
# 
# 
# @router.get("/kinds")
# async def list_kinds():
#     """All addable slide kinds + their field schema, for the 'Add Slide' kind
#     picker — unlike GET /slide/{sid}/{idx}/schema, this isn't scoped to an
#     existing slide (there isn't one yet)."""
#     from backend.services.ppt_template_builder import KIND_FIELD_SCHEMA
#     # "cover" and "thank_you" are structural (auto-added at generation time,
#     # exactly one of each) — not offered as a repeatable "add another" kind.
#     addable = {k: v for k, v in KIND_FIELD_SCHEMA.items() if k not in ("cover", "thank_you")}
#     return {"kinds": addable}
# 
# 
# @router.get("/slide/{session_id}/{slide_index}/schema")
# async def get_slide_schema(session_id: str, slide_index: int):
#     """
#     Kind-aware edit schema for one slide: the field list the frontend needs
#     to render an edit form (e.g. a repeatable name/role list for a "team"
#     slide, an editable grid for a "table" slide) plus that slide's current
#     values. Slides with no known kind (uploaded/legacy decks — see
#     _load_kinds) return {"kind": null}; the frontend falls back to the
#     existing generic title/bullets/notes form in that case, unchanged.
#     """
#     if re.search(r'[/\\.]\.', session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
# 
#     kinds = _load_kinds(session_id)
#     kind = kinds[slide_index] if kinds and 0 <= slide_index < len(kinds) else None
#     if not kind:
#         return {"kind": None}
# 
#     from backend.services.ppt_template_builder import extract_slide_data, KIND_FIELD_SCHEMA
# 
#     def _read():
#         from pptx import Presentation
#         prs = Presentation(pptx_path)
#         if slide_index >= len(prs.slides):
#             return None
#         return extract_slide_data(prs.slides[slide_index], kind)
# 
#     data = await asyncio.to_thread(_read)
#     if data is None:
#         raise HTTPException(400, f"Slide index {slide_index} out of range")
# 
#     return {"kind": kind, "fields": KIND_FIELD_SCHEMA.get(kind, []), "data": data}
# 
# 
# class EditSlideKindReq(BaseModel):
#     session_id:  str
#     slide_index: int
#     kind:        str
#     data:        dict
#     # Notes are a separate field on every kind (not part of any slot map) —
#     # written directly to the notes slide, never through the generic
#     # title/bullet-shape heuristic in _patch_slide, which is exactly what
#     # this whole kind-aware path exists to avoid running against these slides.
#     notes: Optional[str] = None
# 
# 
# @router.patch("/slide/kind")
# async def edit_slide_kind(req: EditSlideKindReq):
#     """Kind-aware counterpart to PATCH /slide — writes structured field data
#     (team members, table rows, comparison columns, ...) via the same
#     slot-map populate functions used at generation time. See
#     services/ppt_template_builder.py."""
#     if re.search(r'[/\\.]\.', req.session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
# 
#     from backend.services.ppt_template_builder import populate_slide_data
# 
#     def _writer(slide):
#         populate_slide_data(slide, req.kind, req.data)
#         if req.notes is not None:
#             try:
#                 notes_tf = slide.notes_slide.notes_text_frame
#                 notes_tf.clear()
#                 para = notes_tf.paragraphs[0] if notes_tf.paragraphs else notes_tf.add_paragraph()
#                 para.add_run().text = req.notes
#             except Exception as e:
#                 logger.warning(f"Could not write speaker notes for slide {req.slide_index}: {e}")
# 
#     slides = await apply_slide_edit_async(req.session_id, req.slide_index, _writer)
#     if slides is None:
#         raise HTTPException(400, f"Slide index {req.slide_index} out of range")
# 
#     return {"status": "ok", "slides": slides}
# 
# 
# def _apply_writer_sync(pptx_path: str, slide_index: int, writer):
#     from pptx import Presentation
#     prs = Presentation(pptx_path)
#     if slide_index >= len(prs.slides):
#         raise ValueError(f"Slide {slide_index} does not exist in the file")
#     writer(prs.slides[slide_index])
#     prs.save(pptx_path)
# 
# 
# async def apply_slide_edit_fast_async(session_id: str, slide_index: int, writer) -> bool:
#     """
#     Records a version snapshot and writes `writer(slide)` to the .pptx file
#     on disk — the fast part of an edit (well under a second). Does NOT
#     regenerate thumbnails or refresh _slide_store; call
#     refresh_slide_thumbnails_async afterward for that.
# 
#     Split out of what's now apply_slide_edit_async below so voice tools can
#     treat thumbnail regeneration as a backgroundable step instead of part of
#     what the user has to wait through before hearing a spoken reply —
#     LibreOffice re-rendering the whole deck (process startup + full-deck
#     render) on every single-field voice edit was adding several seconds to
#     every "I've updated slide N" confirmation, even though the file itself
#     is already correctly written well before that finishes.
#     Returns True on success, False if the session/slide index is invalid.
#     """
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         return False
# 
#     slides_mem = _slide_store.get(session_id, [])
#     if slide_index < 0 or slide_index >= len(slides_mem):
#         return False
# 
#     # Record the previous editable content before applying the mutation. The
#     # in-memory copy satisfies session-duration history even if SQLite is not
#     # available; the DB write below preserves the same record across reloads.
#     # This snapshot is title/bullets/notes-shaped regardless of the writer —
#     # harmless for kind-aware edits (bullets just come back empty, since
#     # kind-aware slides don't use the numbered-bullet convention), just a
#     # less detailed "before" record in the version-diff UI for those.
#     old_slide = slides_mem[slide_index]
#     old_version = _remember_slide_version(session_id, slide_index, old_slide)
# 
#     try:
#         from backend.db.engine import AsyncSessionLocal
#         from backend.db.models import PPTSlideVersion
#         async with AsyncSessionLocal() as db_session:
#             v = PPTSlideVersion(
#                 session_id=session_id,
#                 slide_index=slide_index,
#                 title=old_version["title"],
#                 bullets=json.dumps(old_version["bullets"]),
#                 notes=old_version["notes"]
#             )
#             db_session.add(v)
#             await db_session.commit()
#     except Exception as e:
#         logger.error(f"Failed to record slide version: {e}")
# 
#     await asyncio.to_thread(_apply_writer_sync, pptx_path, slide_index, writer)
#     return True
# 
# 
# async def refresh_slide_thumbnails_async(session_id: str) -> Optional[List[dict]]:
#     """Re-renders thumbnails (LibreOffice, full deck) and refreshes
#     _slide_store from the current .pptx on disk — the slow part of an edit,
#     split out so voice tools can run it as a background task after already
#     replying. Returns the updated slides list, or None if the file is missing."""
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         return None
# 
#     img_dir   = f"data/ppt/slides/{session_id}"
#     img_paths = await asyncio.to_thread(_convert_to_images_sync, pptx_path, img_dir)
# 
#     slides = await _extract_slides(pptx_path)
#     version = int(time.time() * 1000)
#     for i, slide in enumerate(slides):
#         if i < len(img_paths):
#             fname = os.path.basename(img_paths[i])
#             slide["image_url"] = f"/api/v1/ppt/image/{session_id}/{fname}?v={version}"
# 
#     _slide_store[session_id] = slides
#     return slides
# 
# 
# async def apply_slide_edit_async(
#     session_id: str, slide_index: int, writer,
# ) -> Optional[List[dict]]:
#     """
#     Applies a mutation to one slide via `writer(slide)` — an already-open,
#     not-yet-saved slide — then synchronously regenerates thumbnails and
#     updates memory state. Used by the HTTP PATCH endpoints (PATCH /slide,
#     PATCH /slide/kind), whose caller (the Edit modal) is already showing a
#     "Saving…" spinner and needs the updated slides list back in the same
#     response. Voice tools use apply_slide_edit_fast_async +  a backgrounded
#     refresh_slide_thumbnails_async instead, to reply as soon as the file
#     write succeeds rather than waiting for a full-deck LibreOffice
#     re-render too — see tools/ppt_copilot.py.
#     Returns the updated slides list, or None if invalid index.
#     """
#     ok = await apply_slide_edit_fast_async(session_id, slide_index, writer)
#     if not ok:
#         return None
#     return await refresh_slide_thumbnails_async(session_id)
# 
# 
# async def add_slide_fast_async(session_id: str, kind: str, data: dict,
#                                 insert_after: Optional[int] = None) -> Optional[int]:
#     """
#     Inserts a new slide and updates the kinds sidecar — the fast part of
#     adding a slide (no LibreOffice involved, just python-pptx XML writes),
#     split out the same way apply_slide_edit_fast_async is so voice tools can
#     reply as soon as the file write succeeds instead of waiting for a
#     full-deck thumbnail re-render too. Call refresh_slide_thumbnails_async
#     afterward for that. Returns the new slide's index, or None if the
#     session has no presentation on disk.
#     """
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         return None
# 
#     from backend.services.ppt_template_builder import add_slide_to_deck
# 
#     def _current_count() -> int:
#         from pptx import Presentation
#         return len(Presentation(pptx_path).slides)
# 
#     count = await asyncio.to_thread(_current_count)
#     kinds = _load_kinds(session_id) or []
#     if len(kinds) != count:
#         kinds = kinds[:count] + [None] * max(0, count - len(kinds))
# 
#     # Clone an existing same-kind slide already in THIS deck when there is
#     # one — avoids the cross-package image copy entirely for the common
#     # case. Falls back to pulling the kind fresh from the GD template.
#     source_index_in_deck = next((i for i, k in enumerate(kinds) if k == kind), None)
# 
#     new_index = await asyncio.to_thread(
#         add_slide_to_deck, pptx_path, kind, data, source_index_in_deck, insert_after
#     )
# 
#     kinds.insert(new_index, kind)
#     _save_kinds(session_id, kinds)
#     return new_index
# 
# 
# async def add_slide_async(session_id: str, kind: str, data: dict,
#                            insert_after: Optional[int] = None) -> Optional[List[dict]]:
#     """add_slide_fast_async + a synchronous thumbnail refresh — used by the
#     HTTP endpoint below, whose caller needs the updated slides list back in
#     the same response. Voice tools use add_slide_fast_async + a backgrounded
#     refresh_slide_thumbnails_async instead — see tools/ppt_copilot.py."""
#     new_index = await add_slide_fast_async(session_id, kind, data, insert_after)
#     if new_index is None:
#         return None
#     return await refresh_slide_thumbnails_async(session_id)
# 
# 
# class AddSlideReq(BaseModel):
#     session_id:   str
#     kind:         str
#     data:         dict
#     insert_after: Optional[int] = None  # None = append at the end
# 
# 
# @router.post("/slide/add")
# async def add_slide(req: AddSlideReq):
#     """Insert a new slide into an already-generated/uploaded presentation.
#     Unlike PATCH /slide[/kind] (which mutate one existing slide), this
#     creates a new one — see add_slide_async / ppt_template_builder.add_slide_to_deck."""
#     if re.search(r'[/\\.]\.', req.session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
# 
#     from backend.services.ppt_template_builder import KIND_FIELD_SCHEMA
#     if req.kind not in KIND_FIELD_SCHEMA:
#         raise HTTPException(400, f"Unknown slide kind: {req.kind!r}")
# 
#     slides = await add_slide_async(req.session_id, req.kind, req.data, req.insert_after)
#     if slides is None:
#         raise HTTPException(400, "Failed to add slide")
# 
#     return {"status": "ok", "slides": slides, "kinds": _load_kinds(req.session_id)}
# 
# 
# class AddSlideFromInstructionReq(BaseModel):
#     session_id:   str
#     instruction:  str
#     insert_after: Optional[int] = None
# 
# 
# @router.post("/slide/add-generate")
# async def add_slide_from_instruction(req: AddSlideFromInstructionReq):
#     """UI counterpart to the ppt_add_slide voice tool — same one-field 'what
#     should this slide be about' flow as Create PPT, instead of asking the
#     user to pick a kind and fill a structured form by hand. Generates the
#     kind + content from the instruction, falling back to a plain title-only
#     text slide if generation fails or the instruction is empty."""
#     if re.search(r'[/\\.]\.', req.session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
# 
#     from backend.services.ppt_template_builder import generate_single_slide_content
# 
#     instruction = req.instruction.strip()
#     slide_data = None
#     if instruction:
#         try:
#             slide_data = await generate_single_slide_content(instruction)
#         except Exception as e:
#             logger.error(f"add_slide_from_instruction generation error: {e}")
# 
#     if not slide_data:
#         slide_data = {"kind": "text", "title": instruction[:60] or "New Slide", "paragraphs": []}
# 
#     kind = slide_data.pop("kind")
#     slides = await add_slide_async(req.session_id, kind, slide_data, req.insert_after)
#     if slides is None:
#         raise HTTPException(400, "Failed to add slide")
# 
#     return {"status": "ok", "slides": slides, "kinds": _load_kinds(req.session_id),
#             "kind": kind, "title": slide_data.get("title", "")}
# 
# 
# def _patch_notes_batch_sync(pptx_path: str, notes_by_index: dict[int, str]) -> None:
#     """Write speaker notes for multiple slides in a single Presentation open/save."""
#     from pptx import Presentation
# 
#     prs = Presentation(pptx_path)
#     for slide_index, notes in notes_by_index.items():
#         if slide_index >= len(prs.slides):
#             continue
#         slide = prs.slides[slide_index]
#         try:
#             notes_slide = slide.notes_slide
#             notes_tf    = notes_slide.notes_text_frame
#             notes_tf.clear()
#             para = notes_tf.paragraphs[0] if notes_tf.paragraphs else notes_tf.add_paragraph()
#             run  = para.add_run()
#             run.text = notes
#         except Exception as e:
#             logger.warning(f"Could not write speaker notes for slide {slide_index}: {e}")
#     prs.save(pptx_path)
# 
# 
# async def apply_notes_batch_async(session_id: str, notes_by_index: dict[int, str]) -> Optional[List[dict]]:
#     """
#     Writes speaker notes for multiple slides in one file open/save and one
#     thumbnail re-render. apply_slide_edit_async re-renders the WHOLE deck via
#     LibreOffice on every call, so looping it once per slide for a batch op
#     ("generate notes for all slides") would mean N full-deck renders where
#     one suffices.
#     """
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         return None
# 
#     slides_mem = _slide_store.get(session_id, [])
#     for slide_index, old_slide in enumerate(slides_mem):
#         if slide_index in notes_by_index:
#             _remember_slide_version(session_id, slide_index, old_slide)
# 
#     try:
#         from backend.db.engine import AsyncSessionLocal
#         from backend.db.models import PPTSlideVersion
#         async with AsyncSessionLocal() as db_session:
#             for slide_index, old_slide in enumerate(slides_mem):
#                 if slide_index not in notes_by_index:
#                     continue
#                 v = PPTSlideVersion(
#                     session_id=session_id,
#                     slide_index=slide_index,
#                     title=old_slide.get("title", ""),
#                     bullets=json.dumps(_extract_slide_bullets(old_slide)),
#                     notes=old_slide.get("notes", ""),
#                 )
#                 db_session.add(v)
#             await db_session.commit()
#     except Exception as e:
#         logger.error(f"Failed to record slide versions (batch): {e}")
# 
#     await asyncio.to_thread(_patch_notes_batch_sync, pptx_path, notes_by_index)
# 
#     img_dir   = f"data/ppt/slides/{session_id}"
#     img_paths = await asyncio.to_thread(_convert_to_images_sync, pptx_path, img_dir)
# 
#     slides = await _extract_slides(pptx_path)
#     version = int(time.time() * 1000)
#     for i, slide in enumerate(slides):
#         if i < len(img_paths):
#             fname = os.path.basename(img_paths[i])
#             slide["image_url"] = f"/api/v1/ppt/image/{session_id}/{fname}?v={version}"
# 
#     _slide_store[session_id] = slides
#     return slides
# 
# 
# @router.get("/slide/{session_id}/{slide_index}/versions")
# async def get_slide_versions(session_id: str, slide_index: int):
#     """Retrieve all past versions of a slide, ordered newest to oldest."""
#     from backend.db.engine import AsyncSessionLocal
#     from backend.db.models import PPTSlideVersion
#     from sqlalchemy import select
# 
#     try:
#         async with AsyncSessionLocal() as db_session:
#             stmt = (
#                 select(PPTSlideVersion)
#                 .where(PPTSlideVersion.session_id == session_id)
#                 .where(PPTSlideVersion.slide_index == slide_index)
#                 .order_by(PPTSlideVersion.created_at.desc())
#             )
#             result = await db_session.execute(stmt)
#             versions = result.scalars().all()
#             
#             db_versions = [
#                 {
#                     "id": v.id,
#                     "title": v.title,
#                     "bullets": json.loads(v.bullets) if v.bullets else [],
#                     "notes": v.notes,
#                     "created_at": v.created_at.isoformat() if v.created_at else None
#                 } for v in versions
#             ]
#             return {
#                 "status": "ok",
#                 "versions": db_versions or _slide_version_store.get(session_id, {}).get(slide_index, [])
#             }
#     except Exception as e:
#         logger.error(f"Failed to retrieve slide versions: {e}")
#         return {"status": "ok", "versions": _slide_version_store.get(session_id, {}).get(slide_index, [])}
# 
# 
# _BULLET_LINE_RE = re.compile(r'^\d+\.\s{1,3}(.+)')
# 
# 
# def _set_shape_text(shape, new_text: str, *, size_pt: Optional[float] = None, color: Optional[tuple] = None):
#     """Replace a single shape's visible text with `new_text` in its first run,
#     clearing any other runs/paragraphs. Only ever touches the one shape passed
#     in — callers are responsible for picking the correct shape so unrelated
#     shapes on the slide are never modified."""
#     from pptx.util import Pt
#     from pptx.dml.color import RGBColor
# 
#     tf = shape.text_frame
#     for para in tf.paragraphs:
#         for run in para.runs:
#             run.text = ""
#     if tf.paragraphs:
#         first_para = tf.paragraphs[0]
#         if first_para.runs:
#             first_para.runs[0].text = new_text
#             run = first_para.runs[0]
#         else:
#             from pptx.oxml.ns import qn
#             from lxml import etree
#             r_elem = etree.SubElement(first_para._p, qn('a:r'))
#             etree.SubElement(r_elem, qn('a:rPr'), attrib={'lang': 'en-US'})
#             t_elem = etree.SubElement(r_elem, qn('a:t'))
#             t_elem.text = new_text
#             run = None
#         if run is not None:
#             try:
#                 if size_pt is not None:
#                     run.font.size = Pt(size_pt)
#                 if color is not None:
#                     run.font.color.rgb = RGBColor(*color)
#             except Exception:
#                 pass
# 
# 
# def _find_bullet_body_shape(slide, title_shape):
#     """Locate the exact shape that holds the numbered '1. ...' bullet text —
#     i.e. the same shape _extract_slide_bullets() reads from. Returns None if
#     no shape matches, rather than guessing, so callers never overwrite an
#     unrelated text box (subtitle, footer, caption, etc.) by mistake.
# 
#     A shape only qualifies if EVERY non-empty paragraph in it matches the
#     numbered-bullet pattern — not just one. Requiring only one matching
#     paragraph (the previous behaviour) meant a shape with mixed content —
#     e.g. a subtitle/name+date box where only one of several lines happened
#     to look list-like — could be misidentified as "the bullet body" and
#     have its entire text_frame wiped via tf.clear() during an edit that
#     only meant to touch actual numbered bullets.
# 
#     NOTE: python-pptx re-wraps each shape in a fresh proxy object every time
#     `slide.shapes` is iterated, so comparing across two separate loops with
#     `is` (object identity) silently always evaluates False — even for the
#     exact same underlying shape. Compare by `shape_id` (a stable int) instead.
#     This was part of the original bug: the old code's "first shape that is
#     not title_shape" check never actually excluded the title shape.
#     """
#     title_id = getattr(title_shape, "shape_id", None)
#     for shape in slide.shapes:
#         if not shape.has_text_frame or shape.shape_id == title_id:
#             continue
#         non_empty = [p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip()]
#         if non_empty and all(_BULLET_LINE_RE.match(line) for line in non_empty):
#             return shape
#     return None
# 
# 
# def _patch_slide(
#     slide,
#     slide_index: int,
#     title: str,
#     bullets: list[str],
#     notes: str,
#     other_edits: Optional[dict] = None,
# ):
#     """
#     Rewrite a single (already-open) slide's title, numbered-bullet body,
#     speaker notes, and (optionally) any other individual shape's text using
#     python-pptx. Opening/saving the file is the caller's job — see
#     apply_slide_edit_async — so this can be shared with the kind-aware
#     writer, which needs the same version/thumbnail/_slide_store plumbing
#     around a different mutation.
# 
#     Each of title / bullets / notes / other_edits only ever touches the exact
#     shape it corresponds to — nothing else on the slide is cleared or
#     rewritten. This matters because a slide can contain extra shapes (a
#     subtitle, a footer, a caption) that must survive an edit untouched, e.g.
#     "change the title of slide 3" must not wipe unrelated text elsewhere on
#     the slide.
#     """
#     # ── Identify title shape ──────────────────────────────────────────────────
#     title_shape = None
#     for shape in slide.shapes:
#         if shape.has_text_frame and "title" in shape.name.lower():
#             title_shape = shape
#             break
#     # Fallback: first shape with a large font (heuristic for untitled placeholders)
#     if not title_shape:
#         for shape in slide.shapes:
#             if shape.has_text_frame:
#                 for para in shape.text_frame.paragraphs:
#                     for run in para.runs:
#                         try:
#                             if run.font.size and run.font.size.pt >= 24:
#                                 title_shape = shape
#                                 break
#                         except Exception:
#                             pass
#                 if title_shape:
#                     break
# 
#     # ── Identify the bullet/body shape (only if it actually holds bullets) ───
#     # Fixes the bug where "first non-title shape with a text frame" could pick
#     # an unrelated text box and clobber it during a title-only edit.
#     body_shape = _find_bullet_body_shape(slide, title_shape)
# 
#     logger.info(
#         f"_patch_slide slide={slide_index} "
#         f"title_shape={getattr(title_shape, 'shape_id', None)} "
#         f"body_shape={getattr(body_shape, 'shape_id', None)} "
#         f"bullets_provided={len(bullets) if bullets else 0} "
#         f"other_edits_keys={list((other_edits or {}).keys())}"
#     )
# 
#     if title_shape is not None and title is not None:
#         _set_shape_text(title_shape, title)
# 
#     if body_shape is not None and bullets:
#         from pptx.util import Pt
#         from pptx.dml.color import RGBColor
#         tf = body_shape.text_frame
#         tf.clear()  # only ever the confirmed bullet shape, never a guess
#         for i, bullet_text in enumerate(bullets):
#             para = tf.add_paragraph() if i > 0 else tf.paragraphs[0]
#             run = para.add_run()
#             run.text = f"{i + 1}.  {bullet_text}"
#             try:
#                 run.font.size = Pt(17)
#                 run.font.color.rgb = RGBColor(0xCC, 0xCC, 0xCC)
#             except Exception:
#                 pass
# 
#     # ── Update any other individual shape by its stable shape_id ─────────────
#     # Lets edits reach content beyond the title/bullets (a subtitle, callout,
#     # caption, etc.) without risk of touching shapes that weren't named —
#     # each entry addresses exactly one shape by id.
#     if other_edits:
#         handled_ids = {getattr(title_shape, "shape_id", None), getattr(body_shape, "shape_id", None)}
#         for shape in slide.shapes:
#             if not shape.has_text_frame:
#                 continue
#             sid = getattr(shape, "shape_id", None)
#             if sid in handled_ids or sid is None:
#                 continue
#             key = str(sid)
#             if key in other_edits:
#                 _set_shape_text(shape, other_edits[key])
# 
#     # ── Update speaker notes ──────────────────────────────────────────────────
#     if notes is not None:
#         try:
#             notes_slide = slide.notes_slide
#             notes_tf    = notes_slide.notes_text_frame
#             notes_tf.clear()
#             para = notes_tf.paragraphs[0] if notes_tf.paragraphs else notes_tf.add_paragraph()
#             run  = para.add_run()
#             run.text = notes
#         except Exception as e:
#             logger.warning(f"Could not write speaker notes for slide {slide_index}: {e}")
# 
#     logger.info(f"Patched slide {slide_index}")
# 
# 
# @router.get("/history")
# async def get_history():
#     """Return list of all previously generated presentations, newest first."""
#     return {"history": _load_index()}
# 
# 
# @router.get("/history/load/{session_id}")
# async def load_history(session_id: str):
#     """Reload a previously generated PPT into the viewer."""
#     if re.search(r'[/\\.]\.', session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "Presentation file not found on disk")
# 
#     slides = await _extract_slides(pptx_path)
# 
#     # Re-attach slide images if they exist on disk
#     img_dir = f"data/ppt/slides/{session_id}"
#     if os.path.isdir(img_dir):
#         img_paths = sorted(
#             glob.glob(os.path.join(img_dir, "slide-*.png")),
#             key=lambda p: int("".join(filter(str.isdigit, os.path.basename(p))) or "0"),
#         )
#         version = int(os.path.getmtime(pptx_path) * 1000)
#         for i, slide in enumerate(slides):
#             if i < len(img_paths):
#                 fname = os.path.basename(img_paths[i])
#                 slide["image_url"] = f"/api/v1/ppt/image/{session_id}/{fname}?v={version}"
# 
#     # Restore in-memory state so navigate/jump work
#     entry = next((e for e in _load_index() if e["session_id"] == session_id), {})
#     title = entry.get("title", slides[0]["title"] if slides else "Presentation")
#     _slide_store[session_id]  = slides
#     _ppt_titles[session_id]   = title
#     _current_slide[session_id] = 0
# 
#     set_latest_upload_sid(session_id)
# 
#     return {"status": "ok", "slide_count": len(slides), "slides": slides, "title": title}
# 
# 
# async def _extract_slides(path: str) -> list[dict]:
#     """Extract slide metadata (title, shapes, real speaker notes) from pptx."""
#     try:
#         import asyncio
#         return await asyncio.to_thread(_extract_sync, path)
#     except Exception as e:
#         logger.error(f"_extract_slides error: {e}")
#         return [{"index": i, "title": f"Slide {i+1}", "notes": ""} for i in range(10)]
# 
# 
# def _extract_sync(path: str) -> list[dict]:
#     from pptx import Presentation
# 
#     prs = Presentation(path)
#     slides = []
# 
#     def _looks_like_decorative_text(text: str, width_pct: float, rotation: float) -> bool:
#         t = text.strip()
#         tl = t.lower()
#         if not t:
#             return True
#         if re.search(r'\b(?:https?://|www\.|[\w.-]+\.(?:com|org|net|io|ai|co|in))\b', tl):
#             return True
#         if re.fullmatch(r'\d{1,2}(?:\s*/\s*\d{1,2})?', t):
#             return True
#         if re.fullmatch(r'\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}', t):
#             return True
#         if re.fullmatch(r'[A-Za-z]{3,9}\s+\d{4}', t):  # "July 2026" — month name + year, no day
#             return True
#         if abs(rotation or 0) > 1 or width_pct < 8:
#             return True
#         return False
# 
#     # Standard PPTX slide EMU dimensions
#     SLIDE_W = int(prs.slide_width)  or 9144000
#     SLIDE_H = int(prs.slide_height) or 6858000
# 
#     session_id = os.path.splitext(os.path.basename(path))[0]
#     kinds = _load_kinds(session_id)
# 
#     for i, slide in enumerate(prs.slides):
#         kind = kinds[i] if kinds and i < len(kinds) else None
# 
#         # --- Background color ---
#         bg_color = "#111111"
#         try:
#             fill = slide.background.fill
#             if fill.type is not None:
#                 try:
#                     rgb = fill.fore_color.rgb
#                     bg_color = f"#{rgb.r:02X}{rgb.g:02X}{rgb.b:02X}"
#                 except Exception:
#                     pass
#         except Exception:
#             pass
# 
#         # --- Shapes: text + styling + position ---
#         title = ""
#         body  = ""
#         shapes_data: list[dict] = []
#         title_candidates: list[tuple[float, str]] = []
# 
#         for shape in slide.shapes:
#             if not shape.has_text_frame:
#                 continue
#             text = shape.text_frame.text.strip()
#             if not text:
#                 continue
# 
#             text_color = "#FFFFFF"
#             font_size  = 24.0
#             is_bold    = False
#             s_align    = "left"
# 
#             try:
#                 for para in shape.text_frame.paragraphs:
#                     if not para.text.strip():
#                         continue
#                     # Text alignment from first non-empty paragraph
#                     try:
#                         from pptx.enum.text import PP_ALIGN
#                         a = para.alignment
#                         if a == PP_ALIGN.CENTER:      s_align = "center"
#                         elif a == PP_ALIGN.RIGHT:     s_align = "right"
#                         elif a == PP_ALIGN.DISTRIBUTE: s_align = "justify"
#                     except Exception:
#                         pass
#                     for run in para.runs:
#                         try:
#                             if run.font.color.type is not None:
#                                 rgb = run.font.color.rgb
#                                 text_color = f"#{rgb.r:02X}{rgb.g:02X}{rgb.b:02X}"
#                         except Exception:
#                             pass
#                         try:
#                             if run.font.size is not None:
#                                 font_size = run.font.size.pt
#                         except Exception:
#                             pass
#                         try:
#                             if run.font.bold is not None:
#                                 is_bold = run.font.bold
#                         except Exception:
#                             pass
#                         break
#                     break
#             except Exception:
#                 pass
# 
#             # Position as percentage of slide dimensions
#             try:
#                 s_left  = round((shape.left  or 0) / SLIDE_W * 100, 2)
#                 s_top   = round((shape.top   or 0) / SLIDE_H * 100, 2)
#                 s_width = round((shape.width or SLIDE_W) / SLIDE_W * 100, 2)
#                 s_height = round((shape.height or SLIDE_H) / SLIDE_H * 100, 2)
#             except Exception:
#                 s_left, s_top, s_width, s_height = 0.0, 0.0, 90.0, 10.0
#             try:
#                 s_rotation = float(shape.rotation or 0)
#             except Exception:
#                 s_rotation = 0.0
# 
#             # shape_id is a stable python-pptx identifier for this exact shape —
#             # capturing it lets edit operations target one specific shape by id
#             # instead of guessing "the first non-title shape", which is what
#             # previously caused unrelated text boxes to get wiped out during a
#             # title-only or bullet-only edit (see _patch_slide_sync below).
#             try:
#                 s_shape_id = shape.shape_id
#             except Exception:
#                 s_shape_id = None
# 
#             shapes_data.append({
#                 "text":     text,
#                 "color":    text_color,
#                 "size":     min(float(font_size), 80.0),
#                 "bold":     is_bold,
#                 "left":     s_left,
#                 "top":      s_top,
#                 "width":    s_width,
#                 "align":    s_align,
#                 "shape_id": s_shape_id,
#             })
# 
#             # Title extraction is heuristic because uploaded decks often contain
#             # brand marks, vertical URLs, dates, and slide numbers as ordinary
#             # text boxes. Score plausible title text instead of taking the first
#             # large shape in PPTX z-order.
#             decorative = _looks_like_decorative_text(text, s_width, s_rotation)
#             if not decorative:
#                 shape_name = shape.name.lower()
#                 is_placeholder_title = False
#                 try:
#                     is_placeholder_title = "title" in str(shape.placeholder_format.type).lower()
#                 except Exception:
#                     pass
#                 score = float(font_size)
#                 if "title" in shape_name or is_placeholder_title:
#                     score += 80
#                 if is_bold:
#                     score += 12
#                 if 8 <= s_top <= 72:
#                     score += 8
#                 if s_height <= 25:
#                     score += 4
#                 if len(text) <= 55:
#                     score += 6
#                 # A short 2-line shape (e.g. "Presentation Title\nGrid Dynamics" —
#                 # a title stacked with a subtitle in one textbox, as GD template
#                 # cover slides do) is completely normal for a title shape to be.
#                 # Only penalize text that's genuinely body-shaped: more than 2
#                 # lines, or long overall.
#                 if text.count("\n") > 1 or len(text) > 90:
#                     score -= 35
#                 title_candidates.append((score, text))
#             body += text[:120] + " "
# 
#         if title_candidates:
#             title = max(title_candidates, key=lambda item: item[0])[1]
#         if not title and shapes_data:
#             title = next(
#                 (s["text"] for s in shapes_data
#                  if not _looks_like_decorative_text(s["text"], float(s.get("width") or 90), 0)),
#                 shapes_data[0]["text"],
#             )
#         if not title:
#             title = f"Slide {i+1}"
#         # A slide with only one text shape (no separate short title placeholder)
#         # falls through to using that shape's full body text as "title" above —
#         # already penalized in the scoring, but still picked since it's the only
#         # candidate. Cap it here so a 500-character paragraph never ends up as
#         # the displayed slide title (header, thumbnails, browser/OnlyOffice
#         # document title, voice "jump to slide by title" matching, ...); the
#         # full text is still available in each shape's own "text" field.
#         if len(title) > 90:
#             cut = title[:90].rsplit(" ", 1)[0] or title[:90]
#             title = cut.rstrip(" ,.;:") + "…"
# 
#         # ── Real speaker notes (from notes slide, not body text) ──────────────
#         speaker_notes = ""
#         try:
#             if slide.has_notes_slide:
#                 notes_tf = slide.notes_slide.notes_text_frame
#                 # The first paragraph in a notes slide is often a placeholder title;
#                 # collect all non-empty paragraphs after index 0 for the real notes.
#                 note_parts = []
#                 for para in notes_tf.paragraphs:
#                     t = para.text.strip()
#                     if t:
#                         note_parts.append(t)
#                 speaker_notes = "\n".join(note_parts)
#         except Exception:
#             pass
# 
#         # If this deck was generated from the GD template, its "kind" (e.g.
#         # "team", "table") was persisted to a sidecar at generation time
#         # (see _save_kinds) — a .pptx file has no such field of its own.
#         # When known, use the kind-aware extractor's title instead of the
#         # heuristic above: it's always correct (reads the exact shape_id the
#         # generator wrote the title into), whereas the heuristic is a
#         # best-effort guess needed only for uploaded/legacy decks that have
#         # no kind. Uploaded decks have no sidecar, so `kind` stays None and
#         # every kind-aware code path is skipped entirely for them.
#         if kind:
#             try:
#                 from backend.services.ppt_template_builder import extract_slide_data
#                 kind_title = extract_slide_data(slide, kind).get("title")
#                 if kind_title:
#                     title = kind_title
#             except Exception as e:
#                 logger.warning(f"kind-aware title extraction failed for slide {i} ({kind}): {e}")
# 
#         slides.append({
#             "index":    i,
#             "title":    title,
#             "notes":    speaker_notes,
#             "bg_color": bg_color,
#             "shapes":   shapes_data[:15],
#             "kind":     kind,
#         })
# 
#     return slides
# ============================================================================
# ============================================================================
# DISABLED: api/ppt.py transferred from syugesh/pilot-voice-agent-backend
# (feature/ppt-copilot @ commit 869dd08d9). Superseded by the newer
# pilot-voice-agent-backend-ppt_update snapshot, pasted in active below.
# Kept here commented out for reference only — do not import.
# ============================================================================
# """PPT API — navigate + file upload + AI generation with slide extraction."""
# from fastapi import APIRouter, UploadFile, File, Form, HTTPException
# from fastapi.responses import FileResponse
# from pydantic import BaseModel
# from typing import List, Optional
# import os, json, asyncio, subprocess, shutil, glob, logging, time, re, tempfile, json
# from io import BytesIO
#
# logger = logging.getLogger("pilot.ppt")
#
# router = APIRouter()
#
# class PPTCmd(BaseModel):
#     session_id: str
#     direction:  str
#     slide_index: int = -1
#
# class JumpCmd(BaseModel):
#     session_id: str
#     query:      str
#
# _slide_store: dict[str, list[dict]] = {}   # session_id → [{title, index, thumb}]
# _ppt_titles:  dict[str, str]       = {}   # session_id → presentation title (for download filename)
# _latest_upload_sid: str = ""               # fallback key for voice-session lookups
# _current_slide: dict[str, int] = {}        # session_id → 0-indexed current slide
# _slide_version_store: dict[str, dict[int, list[dict]]] = {}  # session_id → slide_index → previous versions
# _ppt_render_locks: dict[str, asyncio.Lock] = {}
#
# _INDEX_PATH = "data/ppt/index.json"
#
#
# def _load_index() -> list[dict]:
#     try:
#         if os.path.exists(_INDEX_PATH):
#             with open(_INDEX_PATH) as f:
#                 return json.load(f)
#     except Exception:
#         pass
#     return []
#
#
# def _kinds_path(session_id: str) -> str:
#     return f"data/ppt/{session_id}.kinds.json"
#
#
# def _save_kinds(session_id: str, kinds: list):
#     """Persist the per-slide template 'kind' list (e.g. "team", "table", or
#     None for slides with no known kind) alongside a generated deck. A .pptx
#     file has no field for "this slide is a team slide" — this sidecar is
#     what lets editing be kind-aware after the fact, since _slide_store is
#     rebuilt from scratch by re-reading the file on every load."""
#     os.makedirs("data/ppt", exist_ok=True)
#     with open(_kinds_path(session_id), "w") as f:
#         json.dump(kinds, f)
#
#
# def _load_kinds(session_id: str) -> list | None:
#     try:
#         path = _kinds_path(session_id)
#         if os.path.exists(path):
#             with open(path) as f:
#                 return json.load(f)
#     except Exception:
#         pass
#     return None
#
#
# def _sources_path(session_id: str) -> str:
#     return f"data/ppt/{session_id}.sources.json"
#
#
# def _save_sources(session_id: str, sources: list):
#     """Persist which exact candidate slide (of possibly several for that
#     kind — see _KIND_SOURCES in ppt_template_builder.py) each slide was
#     cloned from, as "template_path::index" strings aligned 1:1 with the
#     kinds sidecar. Different candidates for the same kind don't share
#     shape_ids, so editing a slide later needs to know precisely which one
#     it came from to compute the matching slot map — just knowing the kind
#     isn't enough once a kind can be built from more than one template slide.
#     """
#     os.makedirs("data/ppt", exist_ok=True)
#     with open(_sources_path(session_id), "w") as f:
#         json.dump(sources, f)
#
#
# def _load_sources(session_id: str) -> list | None:
#     try:
#         path = _sources_path(session_id)
#         if os.path.exists(path):
#             with open(path) as f:
#                 return json.load(f)
#     except Exception:
#         pass
#     return None
#
#
# def _save_index_entry(session_id: str, title: str, description: str, slide_count: int):
#     os.makedirs("data/ppt", exist_ok=True)
#     entries = _load_index()
#     # Remove any prior entry for this session (re-generation)
#     entries = [e for e in entries if e.get("session_id") != session_id]
#     entries.insert(0, {
#         "session_id":  session_id,
#         "title":       title,
#         "description": description,
#         "slide_count": slide_count,
#         "created_at":  __import__("datetime").datetime.now().isoformat(timespec="seconds"),
#     })
#     with open(_INDEX_PATH, "w") as f:
#         json.dump(entries[:50], f, indent=2)   # keep last 50
#
#
# @router.post("/navigate")
# async def navigate(cmd: PPTCmd):
#     from backend.tools.ppt_copilot import ppt_navigate
#     return await ppt_navigate({"direction": cmd.direction}, cmd.session_id)
#
#
# @router.post("/jump")
# async def jump(cmd: JumpCmd):
#     """Fuzzy match slide title → navigate to it."""
#     from backend.queues.bus import bus
#     slides = _slide_store.get(cmd.session_id, [])
#     q = cmd.query.lower()
#     best_idx = -1
#     best_score = 0
#     for s in slides:
#         title = s.get("title","").lower()
#         score = sum(1 for word in q.split() if word in title)
#         # Also match slide number directly: "slide 42" → index 41
#         import re
#         m = re.search(r'\b(\d+)\b', q)
#         if m:
#             num = int(m.group(1)) - 1  # 0-indexed
#             if 0 <= num < len(slides):
#                 best_idx = num
#                 best_score = 99
#                 break
#         if score > best_score:
#             best_score = score
#             best_idx = s.get("index", -1)
#
#     if best_idx >= 0:
#         await bus.emit_event("ppt_command", {"action": "goto", "index": best_idx}, cmd.session_id)
#         title = slides[best_idx]["title"] if best_idx < len(slides) else f"Slide {best_idx+1}"
#         return {"status": "ok", "index": best_idx, "title": title}
#     return {"status": "not_found"}
#
#
# def _find_soffice() -> str | None:
#     """Find LibreOffice executable on macOS or Linux."""
#     candidates = [
#         "/Applications/LibreOffice.app/Contents/MacOS/soffice",
#         shutil.which("libreoffice"),
#         shutil.which("soffice"),
#     ]
#     for c in candidates:
#         if c and os.path.exists(c):
#             return c
#     return None
#
#
# def _find_pdftoppm() -> str | None:
#     candidates = [
#         "/opt/homebrew/bin/pdftoppm",
#         shutil.which("pdftoppm"),
#     ]
#     for c in candidates:
#         if c and os.path.exists(c):
#             return c
#     return None
#
#
# def _convert_to_images_sync(pptx_path: str, out_dir: str, keep_existing_on_failure: bool = True) -> list[str]:
#     """Convert every slide to PNGs without deleting the last good render first."""
#     soffice = _find_soffice()
#     if not soffice:
#         existing = sorted(
#             glob.glob(os.path.join(out_dir, "slide-*.png")),
#             key=lambda p: int("".join(filter(str.isdigit, os.path.basename(p))) or "0"),
#         )
#         return existing if keep_existing_on_failure else []
#     os.makedirs(out_dir, exist_ok=True)
#
#     existing_images = sorted(
#         glob.glob(os.path.join(out_dir, "slide-*.png")),
#         key=lambda p: int("".join(filter(str.isdigit, os.path.basename(p))) or "0"),
#     )
#     work_dir = tempfile.mkdtemp(prefix="ppt-render-")
#
#     try:
#         # Step 1: PPTX → PDF — LibreOffice produces a faithful multi-page PDF.
#         r = subprocess.run(
#             [soffice, "--headless", "--convert-to", "pdf", "--outdir", work_dir, pptx_path],
#             capture_output=True, text=True, timeout=120,
#         )
#         basename = os.path.splitext(os.path.basename(pptx_path))[0]
#         pdf_path = os.path.join(work_dir, f"{basename}.pdf")
#         if r.returncode != 0 or not os.path.exists(pdf_path):
#             logger.error(f"LibreOffice PDF conversion failed: {r.stderr[:300]}")
#             return existing_images if keep_existing_on_failure else []
#
#         # Step 2: PDF pages → PNGs via pdftoppm (installed via: brew install poppler).
#         pdftoppm = _find_pdftoppm()
#         if not pdftoppm:
#             logger.warning("pdftoppm not found — install poppler: brew install poppler")
#             return existing_images if keep_existing_on_failure else []
#
#         prefix = os.path.join(work_dir, "slide")
#         r2 = subprocess.run(
#             [pdftoppm, "-png", "-r", "150", pdf_path, prefix],
#             capture_output=True, text=True, timeout=120,
#         )
#         if r2.returncode != 0:
#             logger.error(f"pdftoppm failed: {r2.stderr[:300]}")
#             return existing_images if keep_existing_on_failure else []
#
#         rendered = sorted(
#             glob.glob(os.path.join(work_dir, "slide-*.png")),
#             key=lambda p: int("".join(filter(str.isdigit, os.path.basename(p))) or "0"),
#         )
#         if not rendered:
#             logger.error("PDF conversion produced no slide images")
#             return existing_images if keep_existing_on_failure else []
#
#         for stale in glob.glob(os.path.join(out_dir, "slide-*.png")) + glob.glob(os.path.join(out_dir, "*.pdf")):
#             try:
#                 os.remove(stale)
#             except OSError:
#                 pass
#
#         final_images: list[str] = []
#         for i, src in enumerate(rendered, start=1):
#             dst = os.path.join(out_dir, f"slide-{i}.png")
#             shutil.move(src, dst)
#             final_images.append(dst)
#
#         logger.info(f"Converted {len(final_images)} slides to PNG")
#         return final_images
#     finally:
#         shutil.rmtree(work_dir, ignore_errors=True)
#
#
# @router.post("/upload")
# async def upload_ppt(session_id: str, file: UploadFile = File(...)):
#     """Accept .pptx, convert slides to images (LibreOffice) + extract text metadata."""
#     if not file.filename.endswith((".pptx", ".ppt")):
#         raise HTTPException(400, "Only .pptx files supported")
#     content = await file.read()
#     os.makedirs("data/ppt", exist_ok=True)
#     path = f"data/ppt/{session_id}.pptx"
#     with open(path, "wb") as f:
#         f.write(content)
#
#     # Convert to slide images (faithful visual) — run in thread (blocking)
#     img_dir   = f"data/ppt/slides/{session_id}"
#     img_paths = await asyncio.to_thread(_convert_to_images_sync, path, img_dir, False)
#
#     # Extract text metadata for voice navigation + summarise
#     slides = await _extract_slides(path)
#
#     # Attach image URL to each slide if conversion succeeded. The version query
#     # param forces the browser to refetch even when session_id + filename are
#     # identical to a previous upload (e.g. repeated uploads into the same
#     # session, or the "default" slot used before a live session exists) —
#     # without it the <img>  src string never changes and the browser keeps
#     # showing the previous deck's already-decoded image.
#     version = int(time.time() * 1000)
#     for i, slide in enumerate(slides):
#         if i < len(img_paths):
#             fname = os.path.basename(img_paths[i])
#             slide["image_url"] = f"/api/v1/ppt/image/{session_id}/{fname}?v={version}"
#
#     global _latest_upload_sid
#     _slide_store[session_id] = slides
#     _latest_upload_sid = session_id
#     _current_slide[session_id] = 0
#     return {"status": "ok", "slide_count": len(slides), "slides": slides}
#
#
# @router.get("/image/{session_id}/{filename}")
# async def serve_slide_image(session_id: str, filename: str):
#     """Serve a converted slide PNG."""
#     # Basic path-traversal guard
#     if ".." in filename or "/" in filename:
#         raise HTTPException(400, "Invalid filename")
#     path = f"data/ppt/slides/{session_id}/{filename}"
#     if not os.path.exists(path):
#         raise HTTPException(404, "Slide image not found")
#     return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})
#
#
# @router.get("/slides/{session_id}")
# async def get_slides(session_id: str):
#     return {"slides": _slide_store.get(session_id, [])}
#
#
# class GenerateRequest(BaseModel):
#     session_id:  str
#     description: str
#     slide_count: int = 10
#
#
# @router.post("/generate")
# async def generate_ppt(req: GenerateRequest):
#     """
#     AI-generate a .pptx from a text description.
#     Uses Ollama (local, free) to write kind-tagged slide content, then clones
#     matching slides out of the real Grid Dynamics template and swaps in that
#     content — see services/ppt_template_builder.py.
#     Returns the same slide format as /upload so the viewer loads immediately.
#     """
#     if not req.description.strip():
#         raise HTTPException(400, "Description cannot be empty")
#     slide_count = max(3, min(req.slide_count, 20))
#
#     # Step 1 — generate kind-tagged slide content with Ollama
#     from backend.services.ppt_template_builder import generate_template_content, build_deck_from_template
#     content = await generate_template_content(req.description, slide_count)
#     if not content:
#         raise HTTPException(502, "Ollama content generation failed — is Ollama running?")
#
#     # Step 2 — clone the matching GD template slides and populate them
#     os.makedirs("data/ppt", exist_ok=True)
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     sources = await asyncio.to_thread(build_deck_from_template, content, pptx_path)
#     _save_kinds(req.session_id, [s.get("kind") for s in content["slides"]])
#     _save_sources(req.session_id, sources)
#
#     # Step 3 — convert to slide images if LibreOffice is available (same as upload)
#     img_dir   = f"data/ppt/slides/{req.session_id}"
#     img_paths = await asyncio.to_thread(_convert_to_images_sync, pptx_path, img_dir, False)
#
#     # Step 4 — extract metadata into the same format the viewer expects
#     slides = await _extract_slides(pptx_path)
#     version = int(time.time() * 1000)
#     for i, slide in enumerate(slides):
#         if i < len(img_paths):
#             fname = os.path.basename(img_paths[i])
#             slide["image_url"] = f"/api/v1/ppt/image/{req.session_id}/{fname}?v={version}"
#
#     global _latest_upload_sid
#     _slide_store[req.session_id] = slides
#     _latest_upload_sid = req.session_id
#     _current_slide[req.session_id] = 0
#
#     title = content.get("presentation_title", "Generated Presentation")
#     _ppt_titles[req.session_id] = title
#     _save_index_entry(req.session_id, title, req.description, len(slides))
#     logger.info(f"Generated '{title}' — {len(slides)} slides for session {req.session_id[:8]}")
#     return {"status": "ok", "slide_count": len(slides), "slides": slides, "title": title}
#
#
# @router.get("/download/{session_id}")
# async def download_ppt(session_id: str):
#     """Download the .pptx file for this session (works for both uploaded and generated files)."""
#     # Basic path-traversal guard
#     if re.search(r'[/\\.]\.', session_id):
#         raise HTTPException(400, "Invalid session id")
#     path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(path):
#         raise HTTPException(404, "No presentation found for this session")
#     raw_title = _ppt_titles.get(session_id) or "presentation"
#     safe_name = re.sub(r'[^\w\s-]', '', raw_title)[:60].strip().replace(' ', '_') or "presentation"
#     return FileResponse(
#         path,
#         media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
#         filename=f"{safe_name}.pptx",
#         headers={"Content-Disposition": f'attachment; filename="{safe_name}.pptx"'},
#     )
#
#
# @router.get("/export-pdf/{session_id}")
# async def export_pdf(session_id: str):
#     """Export the presentation as a PDF using LibreOffice (same pipeline as slide image generation)."""
#     if re.search(r'[/\\.]\.', session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
#
#     soffice = _find_soffice()
#     if not soffice:
#         raise HTTPException(503, "LibreOffice not installed — cannot export PDF")
#
#     raw_title = _ppt_titles.get(session_id) or "presentation"
#     safe_name = re.sub(r'[^\w\s-]', '', raw_title)[:60].strip().replace(' ', '_') or "presentation"
#
#     # Convert in a temp dir so concurrent exports don't collide
#     tmp_dir = tempfile.mkdtemp(prefix="pilot_pdf_")
#     try:
#         r = await asyncio.to_thread(
#             subprocess.run,
#             [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp_dir, pptx_path],
#             capture_output=True, text=True, timeout=120,
#         )
#         basename = os.path.splitext(os.path.basename(pptx_path))[0]
#         pdf_path = os.path.join(tmp_dir, f"{basename}.pdf")
#         if r.returncode != 0 or not os.path.exists(pdf_path):
#             logger.error(f"LibreOffice PDF export failed: {r.stderr[:300]}")
#             raise HTTPException(502, "PDF export failed")
#         return FileResponse(
#             pdf_path,
#             media_type="application/pdf",
#             filename=f"{safe_name}.pdf",
#             headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
#             background=None,  # keep file alive during streaming
#         )
#     except HTTPException:
#         shutil.rmtree(tmp_dir, ignore_errors=True)
#         raise
#     except Exception as e:
#         shutil.rmtree(tmp_dir, ignore_errors=True)
#         logger.error(f"PDF export error: {e}")
#         raise HTTPException(502, f"PDF export error: {e}")
#
#
# class EditSlideReq(BaseModel):
#     session_id:  str
#     slide_index: int
#     title:       str
#     bullets:     List[str]
#     notes:       Optional[str] = ""
#     # Optional map of shape_id (as string) -> new text, for editing any other
#     # shape on the slide (subtitle, caption, footer, etc.) beyond the title
#     # and numbered-bullet body. Defaults to None = no other shapes touched.
#     other_edits: Optional[dict] = None
#
#
# def _extract_slide_bullets(slide: dict) -> list[str]:
#     bullets: list[str] = []
#     for sh in slide.get("shapes", []):
#         for line in sh.get("text", "").split("\n"):
#             m = re.match(r'^\d+\.\s{1,3}(.+)', line)
#             if m:
#                 bullets.append(m.group(1).strip())
#     return bullets
#
#
# def _remember_slide_version(session_id: str, slide_index: int, slide: dict) -> dict:
#     """Capture the editable slide fields before an AI/manual edit mutates them."""
#     version = {
#         "id": None,
#         "title": slide.get("title", ""),
#         "bullets": _extract_slide_bullets(slide),
#         "notes": slide.get("notes", ""),
#         "created_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
#     }
#     _slide_version_store.setdefault(session_id, {}).setdefault(slide_index, []).insert(0, version)
#     return version
#
#
# @router.patch("/slide")
# async def edit_slide(req: EditSlideReq):
#     """
#     Edit the content of a specific slide:
#       - Updates title, bullet text, and speaker notes in the .pptx on disk
#       - Re-renders slide thumbnails (full deck via LibreOffice)
#       - Updates the in-memory _slide_store so the viewer reflects changes immediately
#     """
#     if re.search(r'[/\\.]\.', req.session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
#
#     def _writer(slide):
#         _patch_slide(slide, req.slide_index, req.title, req.bullets, req.notes or "", req.other_edits)
#
#     slides = await apply_slide_edit_async(req.session_id, req.slide_index, _writer)
#     if slides is None:
#         raise HTTPException(400, f"Slide index {req.slide_index} out of range")
#
#     return {"status": "ok", "slides": slides}
#
#
# @router.get("/kinds")
# async def list_kinds():
#     """All addable slide kinds + their field schema, for the 'Add Slide' kind
#     picker — unlike GET /slide/{sid}/{idx}/schema, this isn't scoped to an
#     existing slide (there isn't one yet)."""
#     from backend.services.ppt_template_builder import KIND_FIELD_SCHEMA
#     # "cover" and "thank_you" are structural (auto-added at generation time,
#     # exactly one of each) — not offered as a repeatable "add another" kind.
#     addable = {k: v for k, v in KIND_FIELD_SCHEMA.items() if k not in ("cover", "thank_you")}
#     return {"kinds": addable}
#
#
# @router.get("/slide/{session_id}/{slide_index}/schema")
# async def get_slide_schema(session_id: str, slide_index: int):
#     """
#     Kind-aware edit schema for one slide: the field list the frontend needs
#     to render an edit form (e.g. a repeatable name/role list for a "team"
#     slide, an editable grid for a "table" slide) plus that slide's current
#     values. Slides with no known kind (uploaded/legacy decks — see
#     _load_kinds) return {"kind": null}; the frontend falls back to the
#     existing generic title/bullets/notes form in that case, unchanged.
#     """
#     if re.search(r'[/\\.]\.', session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
#
#     kinds = _load_kinds(session_id)
#     kind = kinds[slide_index] if kinds and 0 <= slide_index < len(kinds) else None
#     if not kind:
#         return {"kind": None}
#
#     sources = _load_sources(session_id)
#     source = sources[slide_index] if sources and 0 <= slide_index < len(sources) else None
#
#     from backend.services.ppt_template_builder import extract_slide_data, KIND_FIELD_SCHEMA, _parse_source
#
#     def _read():
#         from pptx import Presentation
#         prs = Presentation(pptx_path)
#         if slide_index >= len(prs.slides):
#             return None
#         return extract_slide_data(prs.slides[slide_index], kind, _parse_source(source))
#
#     data = await asyncio.to_thread(_read)
#     if data is None:
#         raise HTTPException(400, f"Slide index {slide_index} out of range")
#
#     return {"kind": kind, "fields": KIND_FIELD_SCHEMA.get(kind, []), "data": data}
#
#
# class EditSlideKindReq(BaseModel):
#     session_id:  str
#     slide_index: int
#     kind:        str
#     data:        dict
#     # Notes are a separate field on every kind (not part of any slot map) —
#     # written directly to the notes slide, never through the generic
#     # title/bullet-shape heuristic in _patch_slide, which is exactly what
#     # this whole kind-aware path exists to avoid running against these slides.
#     notes: Optional[str] = None
#
#
# @router.patch("/slide/kind")
# async def edit_slide_kind(req: EditSlideKindReq):
#     """Kind-aware counterpart to PATCH /slide — writes structured field data
#     (team members, table rows, comparison columns, ...) via the same
#     slot-map populate functions used at generation time. See
#     services/ppt_template_builder.py."""
#     if re.search(r'[/\\.]\.', req.session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
#
#     from backend.services.ppt_template_builder import populate_slide_data, _parse_source
#
#     sources = _load_sources(req.session_id)
#     source = sources[req.slide_index] if sources and 0 <= req.slide_index < len(sources) else None
#
#     def _writer(slide):
#         populate_slide_data(slide, req.kind, req.data, _parse_source(source))
#         if req.notes is not None:
#             try:
#                 notes_tf = slide.notes_slide.notes_text_frame
#                 notes_tf.clear()
#                 para = notes_tf.paragraphs[0] if notes_tf.paragraphs else notes_tf.add_paragraph()
#                 para.add_run().text = req.notes
#             except Exception as e:
#                 logger.warning(f"Could not write speaker notes for slide {req.slide_index}: {e}")
#
#     slides = await apply_slide_edit_async(req.session_id, req.slide_index, _writer)
#     if slides is None:
#         raise HTTPException(400, f"Slide index {req.slide_index} out of range")
#
#     return {"status": "ok", "slides": slides}
#
#
# def _apply_writer_sync(pptx_path: str, slide_index: int, writer):
#     from pptx import Presentation
#     prs = Presentation(pptx_path)
#     if slide_index >= len(prs.slides):
#         raise ValueError(f"Slide {slide_index} does not exist in the file")
#     writer(prs.slides[slide_index])
#     prs.save(pptx_path)
#
#
# async def apply_slide_edit_fast_async(session_id: str, slide_index: int, writer) -> bool:
#     """
#     Records a version snapshot and writes `writer(slide)` to the .pptx file
#     on disk — the fast part of an edit (well under a second). Does NOT
#     regenerate thumbnails or refresh _slide_store; call
#     refresh_slide_thumbnails_async afterward for that.
#
#     Split out of what's now apply_slide_edit_async below so voice tools can
#     treat thumbnail regeneration as a backgroundable step instead of part of
#     what the user has to wait through before hearing a spoken reply —
#     LibreOffice re-rendering the whole deck (process startup + full-deck
#     render) on every single-field voice edit was adding several seconds to
#     every "I've updated slide N" confirmation, even though the file itself
#     is already correctly written well before that finishes.
#     Returns True on success, False if the session/slide index is invalid.
#     """
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         return False
#
#     slides_mem = _slide_store.get(session_id, [])
#     if slide_index < 0 or slide_index >= len(slides_mem):
#         return False
#
#     # Record the previous editable content before applying the mutation. The
#     # in-memory copy satisfies session-duration history even if SQLite is not
#     # available; the DB write below preserves the same record across reloads.
#     # This snapshot is title/bullets/notes-shaped regardless of the writer —
#     # harmless for kind-aware edits (bullets just come back empty, since
#     # kind-aware slides don't use the numbered-bullet convention), just a
#     # less detailed "before" record in the version-diff UI for those.
#     old_slide = slides_mem[slide_index]
#     old_version = _remember_slide_version(session_id, slide_index, old_slide)
#
#     try:
#         from backend.db.engine import AsyncSessionLocal
#         from backend.db.models import PPTSlideVersion
#         async with AsyncSessionLocal() as db_session:
#             v = PPTSlideVersion(
#                 session_id=session_id,
#                 slide_index=slide_index,
#                 title=old_version["title"],
#                 bullets=json.dumps(old_version["bullets"]),
#                 notes=old_version["notes"]
#             )
#             db_session.add(v)
#             await db_session.commit()
#     except Exception as e:
#         logger.error(f"Failed to record slide version: {e}")
#
#     await asyncio.to_thread(_apply_writer_sync, pptx_path, slide_index, writer)
#     return True
#
#
# async def refresh_slide_thumbnails_async(session_id: str) -> Optional[List[dict]]:
#     """Re-renders thumbnails (LibreOffice, full deck) and refreshes
#     _slide_store from the current .pptx on disk — the slow part of an edit,
#     split out so voice tools can run it as a background task after already
#     replying. Returns the updated slides list, or None if the file is missing."""
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         return None
#
#     lock = _ppt_render_locks.setdefault(session_id, asyncio.Lock())
#     async with lock:
#         img_dir   = f"data/ppt/slides/{session_id}"
#         img_paths = await asyncio.to_thread(_convert_to_images_sync, pptx_path, img_dir)
#
#         slides = await _extract_slides(pptx_path)
#         version = int(time.time() * 1000)
#         for i, slide in enumerate(slides):
#             if i < len(img_paths):
#                 fname = os.path.basename(img_paths[i])
#                 slide["image_url"] = f"/api/v1/ppt/image/{session_id}/{fname}?v={version}"
#
#         _slide_store[session_id] = slides
#         return slides
#
#
# async def apply_slide_edit_async(
#     session_id: str, slide_index: int, writer,
# ) -> Optional[List[dict]]:
#     """
#     Applies a mutation to one slide via `writer(slide)` — an already-open,
#     not-yet-saved slide — then synchronously regenerates thumbnails and
#     updates memory state. Used by the HTTP PATCH endpoints (PATCH /slide,
#     PATCH /slide/kind), whose caller (the Edit modal) is already showing a
#     "Saving…" spinner and needs the updated slides list back in the same
#     response. Voice tools use apply_slide_edit_fast_async +  a backgrounded
#     refresh_slide_thumbnails_async instead, to reply as soon as the file
#     write succeeds rather than waiting for a full-deck LibreOffice
#     re-render too — see tools/ppt_copilot.py.
#     Returns the updated slides list, or None if invalid index.
#     """
#     ok = await apply_slide_edit_fast_async(session_id, slide_index, writer)
#     if not ok:
#         return None
#     return await refresh_slide_thumbnails_async(session_id)
#
#
# # ── WYSIWYG canvas geometry write ────────────────────────────────────────────
#
# class ShapeGeometry(BaseModel):
#     shape_id: int
#     # All percentages of the slide (0–100), matching what _extract_sync emits.
#     # Any field omitted (None) is left unchanged on that shape.
#     left:   Optional[float] = None
#     top:    Optional[float] = None
#     width:  Optional[float] = None
#     height: Optional[float] = None
#     rotation: Optional[float] = None
#
#
# class SlideGeometryReq(BaseModel):
#     session_id:  str
#     slide_index: int
#     shapes:      List[ShapeGeometry]   # batch: a drag+resize can move several at once
#
#
# def _write_geometry_sync(pptx_path: str, slide_index: int, shapes: List[ShapeGeometry]):
#     """Write shape position/size/rotation to the .pptx. Percent → EMU using the
#     real slide dimensions, keyed by the stable shape_id (never array index, so
#     a canvas edit can't hit the wrong shape). Unknown shape_ids are skipped."""
#     from pptx import Presentation
#     from pptx.util import Emu
#     from backend.services.ppt_template_builder import _shape_by_id
#
#     prs = Presentation(pptx_path)
#     if slide_index >= len(prs.slides):
#         raise ValueError(f"Slide {slide_index} does not exist in the file")
#     slide = prs.slides[slide_index]
#     sw, sh = int(prs.slide_width), int(prs.slide_height)
#
#     for g in shapes:
#         shape = _shape_by_id(slide, g.shape_id)
#         if shape is None:
#             logger.warning(f"geometry: shape_id {g.shape_id} not found on slide {slide_index}")
#             continue
#         if g.left   is not None: shape.left   = Emu(int(sw * g.left   / 100))
#         if g.top    is not None: shape.top    = Emu(int(sh * g.top    / 100))
#         if g.width  is not None: shape.width  = Emu(int(sw * g.width  / 100))
#         if g.height is not None: shape.height = Emu(int(sh * g.height / 100))
#         if g.rotation is not None:
#             try:
#                 shape.rotation = float(g.rotation)
#             except Exception:
#                 pass  # not all shape types support rotation
#     prs.save(pptx_path)
#
#
# async def apply_shape_geometry_async(session_id: str, slide_index: int,
#                                       shapes: List[ShapeGeometry]) -> Optional[List[dict]]:
#     """Fast geometry write + thumbnail refresh, mirroring apply_slide_edit_async."""
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         return None
#     slides_mem = _slide_store.get(session_id, [])
#     if slide_index < 0 or slide_index >= len(slides_mem):
#         return None
#     await asyncio.to_thread(_write_geometry_sync, pptx_path, slide_index, shapes)
#     return await refresh_slide_thumbnails_async(session_id)
#
#
# class ShapeTextReq(BaseModel):
#     session_id:  str
#     slide_index: int
#     shape_id:    int
#     text:        str
#
#
# @router.patch("/slide/shape-text")
# async def edit_shape_text(req: ShapeTextReq):
#     """Set one shape's text by shape_id — the inline-text-edit path behind the
#     WYSIWYG canvas. Reuses _set_shape_text (preserves the shape's existing
#     font styling) rather than the title/bullets-shaped PATCH /slide."""
#     if re.search(r'[/\\.]\.', req.session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
#
#     from backend.services.ppt_template_builder import _shape_by_id
#
#     def _writer(slide):
#         shape = _shape_by_id(slide, req.shape_id)
#         if shape is not None and shape.has_text_frame:
#             _set_shape_text(shape, req.text)
#
#     slides = await apply_slide_edit_async(req.session_id, req.slide_index, _writer)
#     if slides is None:
#         raise HTTPException(400, f"Slide index {req.slide_index} out of range")
#     return {"status": "ok", "slides": slides}
#
#
# def _replace_picture_sync(pptx_path: str, slide_index: int, shape_id: int, image_bytes: bytes):
#     from pptx import Presentation
#     from backend.services.ppt_template_builder import _shape_by_id
#
#     prs = Presentation(pptx_path)
#     if slide_index >= len(prs.slides):
#         raise ValueError(f"Slide {slide_index} does not exist in the file")
#
#     slide = prs.slides[slide_index]
#     shape = _shape_by_id(slide, shape_id)
#     if shape is None:
#         raise ValueError(f"Shape {shape_id} does not exist on slide {slide_index}")
#
#     left, top, width, height = shape.left, shape.top, shape.width, shape.height
#     try:
#         rotation = float(shape.rotation or 0)
#     except Exception:
#         rotation = 0
#
#     # python-pptx has no public "replace image" API. Remove the selected
#     # picture-like shape and insert the uploaded bitmap at the same geometry.
#     shape._element.getparent().remove(shape._element)
#     new_shape = slide.shapes.add_picture(BytesIO(image_bytes), left, top, width=width, height=height)
#     try:
#         new_shape.rotation = rotation
#     except Exception:
#         pass
#     prs.save(pptx_path)
#
#
# @router.patch("/slide/shape-image")
# async def edit_shape_image(
#     session_id: str = Form(...),
#     slide_index: int = Form(...),
#     shape_id: int = Form(...),
#     file: UploadFile = File(...),
# ):
#     """Replace a picture shape from the WYSIWYG canvas, then re-render the
#     LibreOffice preview so the user sees the actual deck output."""
#     if re.search(r'[/\\.]\.', session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
#
#     content_type = (file.content_type or "").lower()
#     if content_type and not content_type.startswith("image/"):
#         raise HTTPException(400, "Only image uploads are supported")
#     image_bytes = await file.read()
#     if not image_bytes:
#         raise HTTPException(400, "Uploaded image is empty")
#
#     try:
#         await asyncio.to_thread(_replace_picture_sync, pptx_path, slide_index, shape_id, image_bytes)
#     except ValueError as e:
#         raise HTTPException(400, str(e))
#
#     slides = await refresh_slide_thumbnails_async(session_id)
#     if slides is None:
#         raise HTTPException(400, f"Slide index {slide_index} out of range")
#     return {"status": "ok", "slides": slides}
#
#
# class ShapeStyleReq(BaseModel):
#     session_id:  str
#     slide_index: int
#     shape_id:    int
#     # All optional — only fields present are changed, matching ShapeGeometry's
#     # partial-update convention so the toolbar can send just what the user toggled.
#     bold:      Optional[bool]  = None
#     italic:    Optional[bool]  = None
#     underline: Optional[bool]  = None
#     font:      Optional[str]   = None
#     size:      Optional[float] = None
#     color:     Optional[str]   = None   # "#RRGGBB"
#     align:     Optional[str]   = None   # left | center | right | justify
#
#
# def _apply_run_style(run, req: "ShapeStyleReq"):
#     from pptx.util import Pt
#     from pptx.dml.color import RGBColor
#     if req.bold is not None:
#         run.font.bold = req.bold
#     if req.italic is not None:
#         run.font.italic = req.italic
#     if req.underline is not None:
#         run.font.underline = req.underline
#     if req.font is not None:
#         run.font.name = req.font
#     if req.size is not None:
#         run.font.size = Pt(req.size)
#     if req.color is not None:
#         hexcolor = req.color.lstrip("#")
#         if len(hexcolor) == 6:
#             run.font.color.rgb = RGBColor.from_string(hexcolor.upper())
#
#
# def _write_shape_style_sync(pptx_path: str, slide_index: int, req: "ShapeStyleReq"):
#     from pptx import Presentation
#     from pptx.enum.text import PP_ALIGN
#     from backend.services.ppt_template_builder import _shape_by_id
#
#     prs = Presentation(pptx_path)
#     if slide_index >= len(prs.slides):
#         raise ValueError(f"Slide {slide_index} does not exist in the file")
#     slide = prs.slides[slide_index]
#     shape = _shape_by_id(slide, req.shape_id)
#     if shape is None or not shape.has_text_frame:
#         raise ValueError(f"Shape {req.shape_id} does not exist or has no text on slide {slide_index}")
#
#     align_map = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
#                  "right": PP_ALIGN.RIGHT, "justify": PP_ALIGN.DISTRIBUTE}
#
#     for para in shape.text_frame.paragraphs:
#         if req.align is not None and req.align in align_map:
#             para.alignment = align_map[req.align]
#         for run in para.runs:
#             _apply_run_style(run, req)
#     prs.save(pptx_path)
#
#
# @router.patch("/slide/shape-style")
# async def edit_shape_style(req: ShapeStyleReq):
#     """Apply text formatting (bold/italic/underline/font/size/color/align) to
#     every run in a shape — the write path behind the canvas toolbar."""
#     if re.search(r'[/\\.]\.', req.session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
#
#     try:
#         await asyncio.to_thread(_write_shape_style_sync, pptx_path, req.slide_index, req)
#     except ValueError as e:
#         raise HTTPException(400, str(e))
#
#     slides = await refresh_slide_thumbnails_async(req.session_id)
#     if slides is None:
#         raise HTTPException(400, f"Slide index {req.slide_index} out of range")
#     return {"status": "ok", "slides": slides}
#
#
# class AddTextBoxReq(BaseModel):
#     session_id:  str
#     slide_index: int
#     text:        str = "New text"
#     left:   float = 35.0
#     top:    float = 40.0
#     width:  float = 30.0
#     height: float = 12.0
#
#
# def _add_textbox_sync(pptx_path: str, req: "AddTextBoxReq") -> int:
#     from pptx import Presentation
#     from pptx.util import Emu, Pt
#
#     prs = Presentation(pptx_path)
#     if req.slide_index >= len(prs.slides):
#         raise ValueError(f"Slide {req.slide_index} does not exist in the file")
#     slide = prs.slides[req.slide_index]
#     sw, sh = int(prs.slide_width), int(prs.slide_height)
#
#     box = slide.shapes.add_textbox(
#         Emu(int(sw * req.left / 100)), Emu(int(sh * req.top / 100)),
#         Emu(int(sw * req.width / 100)), Emu(int(sh * req.height / 100)),
#     )
#     tf = box.text_frame
#     tf.word_wrap = True
#     tf.paragraphs[0].text = req.text
#     run = tf.paragraphs[0].runs[0]
#     run.font.size = Pt(24)
#     prs.save(pptx_path)
#     return box.shape_id
#
#
# @router.post("/slide/shape-add-text")
# async def add_text_box(req: AddTextBoxReq):
#     """Insert a new text box onto the slide at the given percent geometry —
#     the write path behind the canvas toolbar's "Add text box" button."""
#     if re.search(r'[/\\.]\.', req.session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
#
#     try:
#         new_shape_id = await asyncio.to_thread(_add_textbox_sync, pptx_path, req)
#     except ValueError as e:
#         raise HTTPException(400, str(e))
#
#     slides = await refresh_slide_thumbnails_async(req.session_id)
#     if slides is None:
#         raise HTTPException(400, f"Slide index {req.slide_index} out of range")
#     return {"status": "ok", "shape_id": new_shape_id, "slides": slides}
#
#
# class ShapeDeleteReq(BaseModel):
#     session_id:  str
#     slide_index: int
#     shape_id:    int
#
#
# def _delete_shape_sync(pptx_path: str, req: "ShapeDeleteReq"):
#     from pptx import Presentation
#     from backend.services.ppt_template_builder import _shape_by_id
#
#     prs = Presentation(pptx_path)
#     if req.slide_index >= len(prs.slides):
#         raise ValueError(f"Slide {req.slide_index} does not exist in the file")
#     slide = prs.slides[req.slide_index]
#     shape = _shape_by_id(slide, req.shape_id)
#     if shape is None:
#         raise ValueError(f"Shape {req.shape_id} does not exist on slide {req.slide_index}")
#     shape._element.getparent().remove(shape._element)
#     prs.save(pptx_path)
#
#
# @router.delete("/slide/shape")
# async def delete_shape(req: ShapeDeleteReq):
#     """Remove a shape from a slide entirely — the write path behind the
#     canvas toolbar's "Delete" button."""
#     if re.search(r'[/\\.]\.', req.session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
#
#     try:
#         await asyncio.to_thread(_delete_shape_sync, pptx_path, req)
#     except ValueError as e:
#         raise HTTPException(400, str(e))
#
#     slides = await refresh_slide_thumbnails_async(req.session_id)
#     if slides is None:
#         raise HTTPException(400, f"Slide index {req.slide_index} out of range")
#     return {"status": "ok", "slides": slides}
#
#
# class ShapeZOrderReq(BaseModel):
#     session_id:  str
#     slide_index: int
#     shape_id:    int
#     direction:   str   # "front" | "back"
#
#
# def _reorder_shape_sync(pptx_path: str, req: "ShapeZOrderReq"):
#     from pptx import Presentation
#     from backend.services.ppt_template_builder import _shape_by_id
#
#     prs = Presentation(pptx_path)
#     if req.slide_index >= len(prs.slides):
#         raise ValueError(f"Slide {req.slide_index} does not exist in the file")
#     slide = prs.slides[req.slide_index]
#     shape = _shape_by_id(slide, req.shape_id)
#     if shape is None:
#         raise ValueError(f"Shape {req.shape_id} does not exist on slide {req.slide_index}")
#     spTree = shape._element.getparent()
#     el = shape._element
#     spTree.remove(el)
#     if req.direction == "front":
#         spTree.append(el)
#     else:
#         # Insert after the last non-shape (grpSpPr/nvGrpSpPr) element so it
#         # lands at the very back of the drawable shapes, not before them.
#         idx = 0
#         for i, child in enumerate(spTree):
#             if child.tag.endswith("}nvGrpSpPr") or child.tag.endswith("}grpSpPr"):
#                 idx = i + 1
#         spTree.insert(idx, el)
#     prs.save(pptx_path)
#
#
# @router.patch("/slide/shape-zorder")
# async def reorder_shape(req: ShapeZOrderReq):
#     """Bring a shape to front or send it to back — the write path behind the
#     canvas toolbar's layering buttons."""
#     if re.search(r'[/\\.]\.', req.session_id):
#         raise HTTPException(400, "Invalid session id")
#     if req.direction not in ("front", "back"):
#         raise HTTPException(400, "direction must be 'front' or 'back'")
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
#
#     try:
#         await asyncio.to_thread(_reorder_shape_sync, pptx_path, req)
#     except ValueError as e:
#         raise HTTPException(400, str(e))
#
#     slides = await refresh_slide_thumbnails_async(req.session_id)
#     if slides is None:
#         raise HTTPException(400, f"Slide index {req.slide_index} out of range")
#     return {"status": "ok", "slides": slides}
#
#
# @router.patch("/slide/geometry")
# async def edit_slide_geometry(req: SlideGeometryReq):
#     """Move/resize/rotate shapes on a slide — the write path behind the WYSIWYG
#     canvas. Accepts a batch of shape geometry deltas (percent), keyed by the
#     stable shape_id, so a single drag-and-resize is one round-trip."""
#     if re.search(r'[/\\.]\.', req.session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
#     if not req.shapes:
#         raise HTTPException(400, "No shapes provided")
#
#     slides = await apply_shape_geometry_async(req.session_id, req.slide_index, req.shapes)
#     if slides is None:
#         raise HTTPException(400, f"Slide index {req.slide_index} out of range")
#     return {"status": "ok", "slides": slides}
#
#
# async def add_slide_fast_async(session_id: str, kind: str, data: dict,
#                                 insert_after: Optional[int] = None) -> Optional[int]:
#     """
#     Inserts a new slide and updates the kinds sidecar — the fast part of
#     adding a slide (no LibreOffice involved, just python-pptx XML writes),
#     split out the same way apply_slide_edit_fast_async is so voice tools can
#     reply as soon as the file write succeeds instead of waiting for a
#     full-deck thumbnail re-render too. Call refresh_slide_thumbnails_async
#     afterward for that. Returns the new slide's index, or None if the
#     session has no presentation on disk.
#     """
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         return None
#
#     from backend.services.ppt_template_builder import add_slide_to_deck
#
#     def _current_count() -> int:
#         from pptx import Presentation
#         return len(Presentation(pptx_path).slides)
#
#     count = await asyncio.to_thread(_current_count)
#     kinds = _load_kinds(session_id) or []
#     if len(kinds) != count:
#         kinds = kinds[:count] + [None] * max(0, count - len(kinds))
#     sources = _load_sources(session_id) or []
#     if len(sources) != count:
#         sources = sources[:count] + [None] * max(0, count - len(sources))
#
#     # Clone an existing same-kind slide already in THIS deck when there is
#     # one — avoids the cross-package image copy entirely for the common
#     # case, and inherits that slide's exact source so the slot map still
#     # matches. Otherwise a random candidate is pulled from the template
#     # pool (see _KIND_SOURCES) — different each time, on purpose.
#     source_index_in_deck = next((i for i, k in enumerate(kinds) if k == kind), None)
#     existing_source = sources[source_index_in_deck] if source_index_in_deck is not None else None
#
#     new_index, new_source = await asyncio.to_thread(
#         add_slide_to_deck, pptx_path, kind, data, source_index_in_deck, existing_source, insert_after
#     )
#
#     kinds.insert(new_index, kind)
#     sources.insert(new_index, new_source)
#     _save_kinds(session_id, kinds)
#     _save_sources(session_id, sources)
#     return new_index
#
#
# async def add_slide_async(session_id: str, kind: str, data: dict,
#                            insert_after: Optional[int] = None) -> Optional[List[dict]]:
#     """add_slide_fast_async + a synchronous thumbnail refresh — used by the
#     HTTP endpoint below, whose caller needs the updated slides list back in
#     the same response. Voice tools use add_slide_fast_async + a backgrounded
#     refresh_slide_thumbnails_async instead — see tools/ppt_copilot.py."""
#     new_index = await add_slide_fast_async(session_id, kind, data, insert_after)
#     if new_index is None:
#         return None
#     return await refresh_slide_thumbnails_async(session_id)
#
#
# async def reorder_slide_fast_async(session_id: str, from_index: int, to_index: int) -> Optional[int]:
#     """Move a slide within the deck and keep the kinds/sources sidecars in the
#     same order. Fast (XML only); pair with refresh_slide_thumbnails_async for
#     the visual update. Returns the slide's final index, or None if there's no
#     presentation on disk. Raises ValueError on an un-reorderable deck."""
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         return None
#     from backend.services.ppt_template_builder import reorder_slide_in_deck
#
#     final_index = await asyncio.to_thread(reorder_slide_in_deck, pptx_path, from_index, to_index)
#
#     # Mirror the move in the sidecars so kind/source stay attached to the slide.
#     def _reorder_list(lst: list):
#         if 0 <= from_index < len(lst):
#             item = lst.pop(from_index)
#             lst.insert(min(final_index, len(lst)), item)
#         return lst
#     _save_kinds(session_id, _reorder_list(_load_kinds(session_id) or []))
#     _save_sources(session_id, _reorder_list(_load_sources(session_id) or []))
#     return final_index
#
#
# class AddSlideReq(BaseModel):
#     session_id:   str
#     kind:         str
#     data:         dict
#     insert_after: Optional[int] = None  # None = append at the end
#
#
# @router.post("/slide/add")
# async def add_slide(req: AddSlideReq):
#     """Insert a new slide into an already-generated/uploaded presentation.
#     Unlike PATCH /slide[/kind] (which mutate one existing slide), this
#     creates a new one — see add_slide_async / ppt_template_builder.add_slide_to_deck."""
#     if re.search(r'[/\\.]\.', req.session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
#
#     from backend.services.ppt_template_builder import KIND_FIELD_SCHEMA
#     if req.kind not in KIND_FIELD_SCHEMA:
#         raise HTTPException(400, f"Unknown slide kind: {req.kind!r}")
#
#     slides = await add_slide_async(req.session_id, req.kind, req.data, req.insert_after)
#     if slides is None:
#         raise HTTPException(400, "Failed to add slide")
#
#     return {"status": "ok", "slides": slides, "kinds": _load_kinds(req.session_id)}
#
#
# class AddSlideFromInstructionReq(BaseModel):
#     session_id:   str
#     instruction:  str
#     insert_after: Optional[int] = None
#
#
# @router.post("/slide/add-generate")
# async def add_slide_from_instruction(req: AddSlideFromInstructionReq):
#     """UI counterpart to the ppt_add_slide voice tool — same one-field 'what
#     should this slide be about' flow as Create PPT, instead of asking the
#     user to pick a kind and fill a structured form by hand. Generates the
#     kind + content from the instruction, falling back to a plain title-only
#     text slide if generation fails or the instruction is empty."""
#     if re.search(r'[/\\.]\.', req.session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{req.session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "No presentation found for this session")
#
#     from backend.services.ppt_template_builder import generate_single_slide_content
#
#     instruction = req.instruction.strip()
#     slide_data = None
#     if instruction:
#         try:
#             slide_data = await generate_single_slide_content(instruction)
#         except Exception as e:
#             logger.error(f"add_slide_from_instruction generation error: {e}")
#
#     if not slide_data:
#         slide_data = {"kind": "text", "title": instruction[:60] or "New Slide", "paragraphs": []}
#
#     kind = slide_data.pop("kind")
#     slides = await add_slide_async(req.session_id, kind, slide_data, req.insert_after)
#     if slides is None:
#         raise HTTPException(400, "Failed to add slide")
#
#     return {"status": "ok", "slides": slides, "kinds": _load_kinds(req.session_id),
#             "kind": kind, "title": slide_data.get("title", "")}
#
#
# def _patch_notes_batch_sync(pptx_path: str, notes_by_index: dict[int, str]) -> None:
#     """Write speaker notes for multiple slides in a single Presentation open/save."""
#     from pptx import Presentation
#
#     prs = Presentation(pptx_path)
#     for slide_index, notes in notes_by_index.items():
#         if slide_index >= len(prs.slides):
#             continue
#         slide = prs.slides[slide_index]
#         try:
#             notes_slide = slide.notes_slide
#             notes_tf    = notes_slide.notes_text_frame
#             notes_tf.clear()
#             para = notes_tf.paragraphs[0] if notes_tf.paragraphs else notes_tf.add_paragraph()
#             run  = para.add_run()
#             run.text = notes
#         except Exception as e:
#             logger.warning(f"Could not write speaker notes for slide {slide_index}: {e}")
#     prs.save(pptx_path)
#
#
# async def apply_notes_batch_async(session_id: str, notes_by_index: dict[int, str]) -> Optional[List[dict]]:
#     """
#     Writes speaker notes for multiple slides in one file open/save and one
#     thumbnail re-render. apply_slide_edit_async re-renders the WHOLE deck via
#     LibreOffice on every call, so looping it once per slide for a batch op
#     ("generate notes for all slides") would mean N full-deck renders where
#     one suffices.
#     """
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         return None
#
#     slides_mem = _slide_store.get(session_id, [])
#     for slide_index, old_slide in enumerate(slides_mem):
#         if slide_index in notes_by_index:
#             _remember_slide_version(session_id, slide_index, old_slide)
#
#     try:
#         from backend.db.engine import AsyncSessionLocal
#         from backend.db.models import PPTSlideVersion
#         async with AsyncSessionLocal() as db_session:
#             for slide_index, old_slide in enumerate(slides_mem):
#                 if slide_index not in notes_by_index:
#                     continue
#                 v = PPTSlideVersion(
#                     session_id=session_id,
#                     slide_index=slide_index,
#                     title=old_slide.get("title", ""),
#                     bullets=json.dumps(_extract_slide_bullets(old_slide)),
#                     notes=old_slide.get("notes", ""),
#                 )
#                 db_session.add(v)
#             await db_session.commit()
#     except Exception as e:
#         logger.error(f"Failed to record slide versions (batch): {e}")
#
#     await asyncio.to_thread(_patch_notes_batch_sync, pptx_path, notes_by_index)
#
#     img_dir   = f"data/ppt/slides/{session_id}"
#     img_paths = await asyncio.to_thread(_convert_to_images_sync, pptx_path, img_dir)
#
#     slides = await _extract_slides(pptx_path)
#     version = int(time.time() * 1000)
#     for i, slide in enumerate(slides):
#         if i < len(img_paths):
#             fname = os.path.basename(img_paths[i])
#             slide["image_url"] = f"/api/v1/ppt/image/{session_id}/{fname}?v={version}"
#
#     _slide_store[session_id] = slides
#     return slides
#
#
# @router.get("/slide/{session_id}/{slide_index}/versions")
# async def get_slide_versions(session_id: str, slide_index: int):
#     """Retrieve all past versions of a slide, ordered newest to oldest."""
#     from backend.db.engine import AsyncSessionLocal
#     from backend.db.models import PPTSlideVersion
#     from sqlalchemy import select
#
#     try:
#         async with AsyncSessionLocal() as db_session:
#             stmt = (
#                 select(PPTSlideVersion)
#                 .where(PPTSlideVersion.session_id == session_id)
#                 .where(PPTSlideVersion.slide_index == slide_index)
#                 .order_by(PPTSlideVersion.created_at.desc())
#             )
#             result = await db_session.execute(stmt)
#             versions = result.scalars().all()
#
#             db_versions = [
#                 {
#                     "id": v.id,
#                     "title": v.title,
#                     "bullets": json.loads(v.bullets) if v.bullets else [],
#                     "notes": v.notes,
#                     "created_at": v.created_at.isoformat() if v.created_at else None
#                 } for v in versions
#             ]
#             return {
#                 "status": "ok",
#                 "versions": db_versions or _slide_version_store.get(session_id, {}).get(slide_index, [])
#             }
#     except Exception as e:
#         logger.error(f"Failed to retrieve slide versions: {e}")
#         return {"status": "ok", "versions": _slide_version_store.get(session_id, {}).get(slide_index, [])}
#
#
# _BULLET_LINE_RE = re.compile(r'^\d+\.\s{1,3}(.+)')
#
#
# def _set_shape_text(shape, new_text: str, *, size_pt: Optional[float] = None, color: Optional[tuple] = None):
#     """Replace a single shape's visible text with `new_text` in its first run,
#     clearing any other runs/paragraphs. Only ever touches the one shape passed
#     in — callers are responsible for picking the correct shape so unrelated
#     shapes on the slide are never modified."""
#     from pptx.util import Pt
#     from pptx.dml.color import RGBColor
#
#     tf = shape.text_frame
#     for para in tf.paragraphs:
#         for run in para.runs:
#             run.text = ""
#     if tf.paragraphs:
#         first_para = tf.paragraphs[0]
#         if first_para.runs:
#             first_para.runs[0].text = new_text
#             run = first_para.runs[0]
#         else:
#             from pptx.oxml.ns import qn
#             from lxml import etree
#             r_elem = etree.SubElement(first_para._p, qn('a:r'))
#             etree.SubElement(r_elem, qn('a:rPr'), attrib={'lang': 'en-US'})
#             t_elem = etree.SubElement(r_elem, qn('a:t'))
#             t_elem.text = new_text
#             run = None
#         if run is not None:
#             try:
#                 if size_pt is not None:
#                     run.font.size = Pt(size_pt)
#                 if color is not None:
#                     run.font.color.rgb = RGBColor(*color)
#             except Exception:
#                 pass
#
#
# def _find_bullet_body_shape(slide, title_shape):
#     """Locate the exact shape that holds the numbered '1. ...' bullet text —
#     i.e. the same shape _extract_slide_bullets() reads from. Returns None if
#     no shape matches, rather than guessing, so callers never overwrite an
#     unrelated text box (subtitle, footer, caption, etc.) by mistake.
#
#     A shape only qualifies if EVERY non-empty paragraph in it matches the
#     numbered-bullet pattern — not just one. Requiring only one matching
#     paragraph (the previous behaviour) meant a shape with mixed content —
#     e.g. a subtitle/name+date box where only one of several lines happened
#     to look list-like — could be misidentified as "the bullet body" and
#     have its entire text_frame wiped via tf.clear() during an edit that
#     only meant to touch actual numbered bullets.
#
#     NOTE: python-pptx re-wraps each shape in a fresh proxy object every time
#     `slide.shapes` is iterated, so comparing across two separate loops with
#     `is` (object identity) silently always evaluates False — even for the
#     exact same underlying shape. Compare by `shape_id` (a stable int) instead.
#     This was part of the original bug: the old code's "first shape that is
#     not title_shape" check never actually excluded the title shape.
#     """
#     title_id = getattr(title_shape, "shape_id", None)
#     for shape in slide.shapes:
#         if not shape.has_text_frame or shape.shape_id == title_id:
#             continue
#         non_empty = [p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip()]
#         if non_empty and all(_BULLET_LINE_RE.match(line) for line in non_empty):
#             return shape
#     return None
#
#
# def _patch_slide(
#     slide,
#     slide_index: int,
#     title: str,
#     bullets: list[str],
#     notes: str,
#     other_edits: Optional[dict] = None,
# ):
#     """
#     Rewrite a single (already-open) slide's title, numbered-bullet body,
#     speaker notes, and (optionally) any other individual shape's text using
#     python-pptx. Opening/saving the file is the caller's job — see
#     apply_slide_edit_async — so this can be shared with the kind-aware
#     writer, which needs the same version/thumbnail/_slide_store plumbing
#     around a different mutation.
#
#     Each of title / bullets / notes / other_edits only ever touches the exact
#     shape it corresponds to — nothing else on the slide is cleared or
#     rewritten. This matters because a slide can contain extra shapes (a
#     subtitle, a footer, a caption) that must survive an edit untouched, e.g.
#     "change the title of slide 3" must not wipe unrelated text elsewhere on
#     the slide.
#     """
#     # ── Identify title shape ──────────────────────────────────────────────────
#     title_shape = None
#     for shape in slide.shapes:
#         if shape.has_text_frame and "title" in shape.name.lower():
#             title_shape = shape
#             break
#     # Fallback: first shape with a large font (heuristic for untitled placeholders)
#     if not title_shape:
#         for shape in slide.shapes:
#             if shape.has_text_frame:
#                 for para in shape.text_frame.paragraphs:
#                     for run in para.runs:
#                         try:
#                             if run.font.size and run.font.size.pt >= 24:
#                                 title_shape = shape
#                                 break
#                         except Exception:
#                             pass
#                 if title_shape:
#                     break
#
#     # ── Identify the bullet/body shape (only if it actually holds bullets) ───
#     # Fixes the bug where "first non-title shape with a text frame" could pick
#     # an unrelated text box and clobber it during a title-only edit.
#     body_shape = _find_bullet_body_shape(slide, title_shape)
#
#     logger.info(
#         f"_patch_slide slide={slide_index} "
#         f"title_shape={getattr(title_shape, 'shape_id', None)} "
#         f"body_shape={getattr(body_shape, 'shape_id', None)} "
#         f"bullets_provided={len(bullets) if bullets else 0} "
#         f"other_edits_keys={list((other_edits or {}).keys())}"
#     )
#
#     if title_shape is not None and title is not None:
#         _set_shape_text(title_shape, title)
#
#     if body_shape is not None and bullets:
#         from pptx.util import Pt
#         from pptx.dml.color import RGBColor
#         tf = body_shape.text_frame
#         tf.clear()  # only ever the confirmed bullet shape, never a guess
#         for i, bullet_text in enumerate(bullets):
#             para = tf.add_paragraph() if i > 0 else tf.paragraphs[0]
#             run = para.add_run()
#             run.text = f"{i + 1}.  {bullet_text}"
#             try:
#                 run.font.size = Pt(17)
#                 run.font.color.rgb = RGBColor(0xCC, 0xCC, 0xCC)
#             except Exception:
#                 pass
#
#     # ── Update any other individual shape by its stable shape_id ─────────────
#     # Lets edits reach content beyond the title/bullets (a subtitle, callout,
#     # caption, etc.) without risk of touching shapes that weren't named —
#     # each entry addresses exactly one shape by id.
#     if other_edits:
#         handled_ids = {getattr(title_shape, "shape_id", None), getattr(body_shape, "shape_id", None)}
#         for shape in slide.shapes:
#             if not shape.has_text_frame:
#                 continue
#             sid = getattr(shape, "shape_id", None)
#             if sid in handled_ids or sid is None:
#                 continue
#             key = str(sid)
#             if key in other_edits:
#                 _set_shape_text(shape, other_edits[key])
#
#     # ── Update speaker notes ──────────────────────────────────────────────────
#     if notes is not None:
#         try:
#             notes_slide = slide.notes_slide
#             notes_tf    = notes_slide.notes_text_frame
#             notes_tf.clear()
#             para = notes_tf.paragraphs[0] if notes_tf.paragraphs else notes_tf.add_paragraph()
#             run  = para.add_run()
#             run.text = notes
#         except Exception as e:
#             logger.warning(f"Could not write speaker notes for slide {slide_index}: {e}")
#
#     logger.info(f"Patched slide {slide_index}")
#
#
# @router.get("/history")
# async def get_history():
#     """Return list of all previously generated presentations, newest first."""
#     return {"history": _load_index()}
#
#
# @router.get("/history/load/{session_id}")
# async def load_history(session_id: str):
#     """Reload a previously generated PPT into the viewer."""
#     if re.search(r'[/\\.]\.', session_id):
#         raise HTTPException(400, "Invalid session id")
#     pptx_path = f"data/ppt/{session_id}.pptx"
#     if not os.path.exists(pptx_path):
#         raise HTTPException(404, "Presentation file not found on disk")
#
#     slides = await _extract_slides(pptx_path)
#
#     # Re-attach slide images if they exist on disk
#     img_dir = f"data/ppt/slides/{session_id}"
#     if os.path.isdir(img_dir):
#         img_paths = sorted(
#             glob.glob(os.path.join(img_dir, "slide-*.png")),
#             key=lambda p: int("".join(filter(str.isdigit, os.path.basename(p))) or "0"),
#         )
#         version = int(os.path.getmtime(pptx_path) * 1000)
#         for i, slide in enumerate(slides):
#             if i < len(img_paths):
#                 fname = os.path.basename(img_paths[i])
#                 slide["image_url"] = f"/api/v1/ppt/image/{session_id}/{fname}?v={version}"
#
#     # Restore in-memory state so navigate/jump work
#     entry = next((e for e in _load_index() if e["session_id"] == session_id), {})
#     title = entry.get("title", slides[0]["title"] if slides else "Presentation")
#     _slide_store[session_id]  = slides
#     _ppt_titles[session_id]   = title
#     _current_slide[session_id] = 0
#
#     global _latest_upload_sid
#     _latest_upload_sid = session_id
#
#     return {"status": "ok", "slide_count": len(slides), "slides": slides, "title": title}
#
#
# async def _extract_slides(path: str) -> list[dict]:
#     """Extract slide metadata (title, shapes, real speaker notes) from pptx."""
#     try:
#         import asyncio
#         return await asyncio.to_thread(_extract_sync, path)
#     except Exception as e:
#         logger.error(f"_extract_slides error: {e}")
#         return [{"index": i, "title": f"Slide {i+1}", "notes": ""} for i in range(10)]
#
#
# def _extract_sync(path: str) -> list[dict]:
#     from pptx import Presentation
#
#     prs = Presentation(path)
#     slides = []
#
#     def _looks_like_decorative_text(text: str, width_pct: float, rotation: float) -> bool:
#         t = text.strip()
#         tl = t.lower()
#         if not t:
#             return True
#         if re.search(r'\b(?:https?://|www\.|[\w.-]+\.(?:com|org|net|io|ai|co|in))\b', tl):
#             return True
#         if re.fullmatch(r'\d{1,2}(?:\s*/\s*\d{1,2})?', t):
#             return True
#         if re.fullmatch(r'\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}', t):
#             return True
#         if re.fullmatch(r'[A-Za-z]{3,9}\s+\d{4}', t):  # "July 2026" — month name + year, no day
#             return True
#         if abs(rotation or 0) > 1 or width_pct < 8:
#             return True
#         return False
#
#     # Standard PPTX slide EMU dimensions
#     SLIDE_W = int(prs.slide_width)  or 9144000
#     SLIDE_H = int(prs.slide_height) or 6858000
#
#     session_id = os.path.splitext(os.path.basename(path))[0]
#     kinds = _load_kinds(session_id)
#     sources = _load_sources(session_id)
#
#     for i, slide in enumerate(prs.slides):
#         kind = kinds[i] if kinds and i < len(kinds) else None
#         source = sources[i] if sources and i < len(sources) else None
#
#         # --- Background color ---
#         bg_color = "#111111"
#         try:
#             fill = slide.background.fill
#             if fill.type is not None:
#                 try:
#                     rgb = fill.fore_color.rgb
#                     bg_color = f"#{rgb.r:02X}{rgb.g:02X}{rgb.b:02X}"
#                 except Exception:
#                     pass
#         except Exception:
#             pass
#
#         # --- Shapes: text/images + styling + position ---
#         title = ""
#         body  = ""
#         shapes_data: list[dict] = []
#         title_candidates: list[tuple[float, str]] = []
#
#         for shape in slide.shapes:
#             try:
#                 s_left  = round((shape.left  or 0) / SLIDE_W * 100, 2)
#                 s_top   = round((shape.top   or 0) / SLIDE_H * 100, 2)
#                 s_width = round((shape.width or SLIDE_W) / SLIDE_W * 100, 2)
#                 s_height = round((shape.height or SLIDE_H) / SLIDE_H * 100, 2)
#             except Exception:
#                 s_left, s_top, s_width, s_height = 0.0, 0.0, 90.0, 10.0
#             try:
#                 s_rotation = float(shape.rotation or 0)
#             except Exception:
#                 s_rotation = 0.0
#             try:
#                 s_shape_id = shape.shape_id
#             except Exception:
#                 s_shape_id = None
#
#             is_picture = False
#             try:
#                 from pptx.enum.shapes import MSO_SHAPE_TYPE
#                 is_picture = shape.shape_type == MSO_SHAPE_TYPE.PICTURE
#             except Exception:
#                 is_picture = "picture" in str(getattr(shape, "shape_type", "")).lower()
#
#             if is_picture:
#                 shapes_data.append({
#                     "text":     "",
#                     "color":    "#000000",
#                     "size":     0,
#                     "bold":     False,
#                     "left":     s_left,
#                     "top":      s_top,
#                     "width":    s_width,
#                     "height":   s_height,
#                     "rotation": s_rotation,
#                     "align":    "left",
#                     "shape_id": s_shape_id,
#                     "type":     "image",
#                 })
#                 continue
#
#             if not shape.has_text_frame:
#                 continue
#             text = shape.text_frame.text.strip()
#             if not text:
#                 continue
#
#             # None until a run reports an explicit RGB override — many shapes
#             # inherit their color from the theme/layout, which python-pptx
#             # cannot resolve to a concrete RGB, so we must not silently guess
#             # one (previously defaulted to white, which lied to the toolbar's
#             # color swatch on light-background decks).
#             text_color: Optional[str] = None
#             font_size  = 24.0
#             is_bold    = False
#             is_italic  = False
#             is_underline = False
#             font_name  = None
#             s_align    = "left"
#
#             try:
#                 for para in shape.text_frame.paragraphs:
#                     if not para.text.strip():
#                         continue
#                     # Text alignment from first non-empty paragraph
#                     try:
#                         from pptx.enum.text import PP_ALIGN
#                         a = para.alignment
#                         if a == PP_ALIGN.CENTER:      s_align = "center"
#                         elif a == PP_ALIGN.RIGHT:     s_align = "right"
#                         elif a == PP_ALIGN.DISTRIBUTE: s_align = "justify"
#                     except Exception:
#                         pass
#                     for run in para.runs:
#                         try:
#                             if run.font.color.type is not None:
#                                 rgb = run.font.color.rgb
#                                 text_color = f"#{rgb.r:02X}{rgb.g:02X}{rgb.b:02X}"
#                         except Exception:
#                             pass
#                         try:
#                             if run.font.size is not None:
#                                 font_size = run.font.size.pt
#                         except Exception:
#                             pass
#                         try:
#                             if run.font.bold is not None:
#                                 is_bold = run.font.bold
#                         except Exception:
#                             pass
#                         try:
#                             if run.font.italic is not None:
#                                 is_italic = run.font.italic
#                         except Exception:
#                             pass
#                         try:
#                             if run.font.underline is not None:
#                                 is_underline = bool(run.font.underline)
#                         except Exception:
#                             pass
#                         try:
#                             if run.font.name is not None:
#                                 font_name = run.font.name
#                         except Exception:
#                             pass
#                         break
#                     break
#             except Exception:
#                 pass
#
#             shapes_data.append({
#                 "text":     text,
#                 "color":    text_color,
#                 "size":     min(float(font_size), 80.0),
#                 "bold":     is_bold,
#                 "italic":   is_italic,
#                 "underline": is_underline,
#                 "font":     font_name,
#                 "left":     s_left,
#                 "top":      s_top,
#                 "width":    s_width,
#                 "height":   s_height,      # emitted for the WYSIWYG canvas (was computed, never sent)
#                 "rotation": s_rotation,
#                 "align":    s_align,
#                 "shape_id": s_shape_id,
#                 "type":     "text",
#             })
#
#             # Title extraction is heuristic because uploaded decks often contain
#             # brand marks, vertical URLs, dates, and slide numbers as ordinary
#             # text boxes. Score plausible title text instead of taking the first
#             # large shape in PPTX z-order.
#             decorative = _looks_like_decorative_text(text, s_width, s_rotation)
#             if not decorative:
#                 shape_name = shape.name.lower()
#                 is_placeholder_title = False
#                 try:
#                     is_placeholder_title = "title" in str(shape.placeholder_format.type).lower()
#                 except Exception:
#                     pass
#                 score = float(font_size)
#                 if "title" in shape_name or is_placeholder_title:
#                     score += 80
#                 if is_bold:
#                     score += 12
#                 if 8 <= s_top <= 72:
#                     score += 8
#                 if s_height <= 25:
#                     score += 4
#                 if len(text) <= 55:
#                     score += 6
#                 # A short 2-line shape (e.g. "Presentation Title\nGrid Dynamics" —
#                 # a title stacked with a subtitle in one textbox, as GD template
#                 # cover slides do) is completely normal for a title shape to be.
#                 # Only penalize text that's genuinely body-shaped: more than 2
#                 # lines, or long overall.
#                 if text.count("\n") > 1 or len(text) > 90:
#                     score -= 35
#                 title_candidates.append((score, text))
#             body += text[:120] + " "
#
#         if title_candidates:
#             title = max(title_candidates, key=lambda item: item[0])[1]
#         if not title and shapes_data:
#             title = next(
#                 (s["text"] for s in shapes_data
#                  if not _looks_like_decorative_text(s["text"], float(s.get("width") or 90), 0)),
#                 shapes_data[0]["text"],
#             )
#         if not title:
#             title = f"Slide {i+1}"
#
#         # ── Real speaker notes (from notes slide, not body text) ──────────────
#         speaker_notes = ""
#         try:
#             if slide.has_notes_slide:
#                 notes_tf = slide.notes_slide.notes_text_frame
#                 # The first paragraph in a notes slide is often a placeholder title;
#                 # collect all non-empty paragraphs after index 0 for the real notes.
#                 note_parts = []
#                 for para in notes_tf.paragraphs:
#                     t = para.text.strip()
#                     if t:
#                         note_parts.append(t)
#                 speaker_notes = "\n".join(note_parts)
#         except Exception:
#             pass
#
#         # If this deck was generated from the GD template, its "kind" (e.g.
#         # "team", "table") was persisted to a sidecar at generation time
#         # (see _save_kinds) — a .pptx file has no such field of its own.
#         # When known, use the kind-aware extractor's title instead of the
#         # heuristic above: it's always correct (reads the exact shape_id the
#         # generator wrote the title into), whereas the heuristic is a
#         # best-effort guess needed only for uploaded/legacy decks that have
#         # no kind. Uploaded decks have no sidecar, so `kind` stays None and
#         # every kind-aware code path is skipped entirely for them.
#         if kind:
#             try:
#                 from backend.services.ppt_template_builder import extract_slide_data, _parse_source
#                 kind_title = extract_slide_data(slide, kind, _parse_source(source)).get("title")
#                 if kind_title:
#                     title = kind_title
#             except Exception as e:
#                 logger.warning(f"kind-aware title extraction failed for slide {i} ({kind}): {e}")
#
#         slides.append({
#             "index":    i,
#             "title":    title,
#             "notes":    speaker_notes,
#             "bg_color": bg_color,
#             # Raised from 15 → 60 so the WYSIWYG canvas gets every editable
#             # shape (a busy slide can exceed 15); the title heuristic above
#             # already filters decoratively so extra shapes don't affect it.
#             "shapes":   shapes_data[:60],
#             "kind":     kind,
#             "source":   source,
#             # EMU slide dimensions so the frontend can build an aspect-correct
#             # canvas and convert between its pixels and the shape percentages.
#             "slide_width":  SLIDE_W,
#             "slide_height": SLIDE_H,
#         })
#
#     return slides
# ============================================================================
# ACTIVE: api/ppt.py transferred from pilot-voice-agent-backend-ppt_update
# (newer snapshot, supersedes the feature/ppt-copilot @ 869dd08d9 version
# above). Import paths adapted from bare (api./core./services./tools./db./
# queues.) to PILOT's backend.-prefixed absolute-import convention; no
# other logic changes.
# ============================================================================
"""PPT API — navigate + file upload + AI generation with slide extraction."""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
import os, json, asyncio, subprocess, shutil, glob, logging, time, re, tempfile, json
from io import BytesIO

logger = logging.getLogger("pilot.ppt")

router = APIRouter()


def _user_id_from_token(authorization: str | None) -> str | None:
    """Same pattern as api/sessions.py's _claims_from_token — extracts the
    JWT 'sub' claim (the user id) so PPT history can be scoped per user
    instead of a single shared list every account could see and click into."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        from backend.core.security import decode_token
        claims = decode_token(authorization.split(" ", 1)[1])
        return str(claims["sub"])
    except Exception:
        return None

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


def _save_index_entry(session_id: str, title: str, description: str, slide_count: int,
                       user_id: str | None = None):
    os.makedirs("data/ppt", exist_ok=True)
    entries = _load_index()
    # Remove any prior entry for this exact session_id (re-generation of the
    # SAME deck replaces its own entry — this is not the MRU reordering; each
    # genuinely distinct upload/generation now gets its own session_id, see
    # newAnonPptSid() on the frontend, so this only ever collides with itself).
    entries = [e for e in entries if e.get("session_id") != session_id]
    now = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    entries.insert(0, {
        "session_id":   session_id,
        "title":        title,
        "description":  description,
        "slide_count":  slide_count,
        "user_id":      user_id,
        "created_at":   now,
        "last_used_at": now,
    })
    with open(_INDEX_PATH, "w") as f:
        json.dump(entries[:50], f, indent=2)   # keep last 50


def _touch_index_entry(session_id: str):
    """Bump this entry's last_used_at to now — called whenever a deck is
    reopened from history, so the list behaves as true most-recently-USED
    (not just most-recently-created): opening an old presentation brings it
    back to the top, same as a browser's history or an LRU cache."""
    entries = _load_index()
    for e in entries:
        if e.get("session_id") == session_id:
            e["last_used_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
            break
    with open(_INDEX_PATH, "w") as f:
        json.dump(entries, f, indent=2)


async def _autofill_speaker_notes(session_id: str):
    """Fire-and-forget background task: generate speaker notes for every
    slide that doesn't already have them, right after a deck is uploaded or
    created — no user action required. Runs AFTER the endpoint has already
    returned the deck to the frontend (which shows it immediately and polls
    /slides/{sid} for notes to appear — see PPTView.tsx), since notes
    generation is a real per-slide LLM call and blocking the initial
    upload/create response on it would mean 10s-90s+ of dead wait depending
    on deck size and provider latency.
    """
    try:
        from backend.services.ppt_template_builder import generate_notes_for_deck
        slides = _slide_store.get(session_id, [])
        if not slides:
            return
        notes_by_index = await generate_notes_for_deck(slides)
        if not notes_by_index:
            logger.info(f"[{session_id[:8]}] auto-notes: nothing to generate")
            return
        await apply_notes_batch_async(session_id, notes_by_index)
        logger.info(f"[{session_id[:8]}] auto-notes: filled {len(notes_by_index)}/{len(slides)} slides")
    except Exception as e:
        logger.error(f"[{session_id[:8]}] auto-notes background task failed: {e}", exc_info=True)


@router.post("/navigate")
async def navigate(cmd: PPTCmd):
    from backend.tools.ppt_copilot import ppt_navigate
    return await ppt_navigate({"direction": cmd.direction}, cmd.session_id)


@router.post("/jump")
async def jump(cmd: JumpCmd):
    """Fuzzy match slide title → navigate to it."""
    from backend.queues.bus import bus
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
async def upload_ppt(session_id: str, file: UploadFile = File(...),
                      authorization: str | None = Header(None)):
    """Accept .pptx, convert slides to images (LibreOffice) + extract text metadata."""
    user_id = _user_id_from_token(authorization)
    if not user_id:
        raise HTTPException(401, "Session expired — please log in again")
    if not file.filename.endswith((".pptx", ".ppt")):
        raise HTTPException(400, "Only .pptx files supported")

    title = os.path.splitext(file.filename)[0]
    # Re-uploading a file with the same name is treated as re-using that same
    # deck (updating it), not creating a second history row for it — redirect
    # onto the EXISTING entry's session_id so this upload overwrites that slot
    # in place rather than the frontend's freshly-minted id creating a
    # duplicate. Scoped to this user only.
    existing = next(
        (e for e in _load_index() if e.get("user_id") == user_id and e.get("title") == title),
        None,
    )
    if existing:
        session_id = existing["session_id"]

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

    _ppt_titles[session_id] = title
    _save_index_entry(session_id, title, "Uploaded presentation", len(slides), user_id=user_id)

    asyncio.create_task(_autofill_speaker_notes(session_id))

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
async def generate_ppt(req: GenerateRequest, authorization: str | None = Header(None)):
    """
    AI-generate a .pptx from a text description.
    Uses Ollama (local, free) to write kind-tagged slide content, then clones
    matching slides out of the real Grid Dynamics template and swaps in that
    content — see services/ppt_template_builder.py.
    Returns the same slide format as /upload so the viewer loads immediately.
    """
    user_id = _user_id_from_token(authorization)
    if not user_id:
        # Missing OR expired token (access tokens last 15 min — see
        # core/config.py). Failing loudly here beats silently generating
        # the deck with user_id=None: an orphaned entry nobody's history
        # filter will ever show again (see api/ppt.py history endpoints).
        raise HTTPException(401, "Session expired — please log in again")
    if not req.description.strip():
        raise HTTPException(400, "Description cannot be empty")
    slide_count = max(3, min(req.slide_count, 20))

    # Step 1 — generate kind-tagged slide content with Ollama
    from backend.services.ppt_template_builder import generate_template_content, build_deck_from_template
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
    _save_index_entry(req.session_id, title, req.description, len(slides), user_id=user_id)
    logger.info(f"Generated '{title}' — {len(slides)} slides for session {req.session_id[:8]}")

    asyncio.create_task(_autofill_speaker_notes(req.session_id))

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
    from backend.services.ppt_template_builder import KIND_FIELD_SCHEMA
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

    from backend.services.ppt_template_builder import extract_slide_data, KIND_FIELD_SCHEMA, _parse_source

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

    from backend.services.ppt_template_builder import populate_slide_data, _parse_source

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
        from backend.db.engine import AsyncSessionLocal
        from backend.db.models import PPTSlideVersion
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
    from backend.services.ppt_template_builder import _shape_by_id

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

    from backend.services.ppt_template_builder import _shape_by_id

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
    from backend.services.ppt_template_builder import _shape_by_id

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
    from backend.services.ppt_template_builder import _shape_by_id

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
    from backend.services.ppt_template_builder import _shape_by_id

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
    from backend.services.ppt_template_builder import _shape_by_id

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

    from backend.services.ppt_template_builder import add_slide_to_deck

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


async def reorder_slide_fast_async(session_id: str, from_index: int, to_index: int) -> Optional[int]:
    """Move a slide within the deck and keep the kinds/sources sidecars in the
    same order. Fast (XML only); pair with refresh_slide_thumbnails_async for
    the visual update. Returns the slide's final index, or None if there's no
    presentation on disk. Raises ValueError on an un-reorderable deck."""
    pptx_path = f"data/ppt/{session_id}.pptx"
    if not os.path.exists(pptx_path):
        return None
    from backend.services.ppt_template_builder import reorder_slide_in_deck

    final_index = await asyncio.to_thread(reorder_slide_in_deck, pptx_path, from_index, to_index)

    # Mirror the move in the sidecars so kind/source stay attached to the slide.
    def _reorder_list(lst: list):
        if 0 <= from_index < len(lst):
            item = lst.pop(from_index)
            lst.insert(min(final_index, len(lst)), item)
        return lst
    _save_kinds(session_id, _reorder_list(_load_kinds(session_id) or []))
    _save_sources(session_id, _reorder_list(_load_sources(session_id) or []))
    return final_index


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

    from backend.services.ppt_template_builder import KIND_FIELD_SCHEMA
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

    from backend.services.ppt_template_builder import generate_single_slide_content

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
        from backend.db.engine import AsyncSessionLocal
        from backend.db.models import PPTSlideVersion
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
    from backend.db.engine import AsyncSessionLocal
    from backend.db.models import PPTSlideVersion
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
async def get_history(authorization: str | None = Header(None)):
    """Return this user's previously generated/uploaded presentations,
    most-recently-USED first (opening an old one from history bumps it back
    to the top — see _touch_index_entry). Scoped to the logged-in user —
    entries saved before user_id was tracked (user_id is None) belong to
    nobody and are excluded."""
    user_id = _user_id_from_token(authorization)
    if not user_id:
        raise HTTPException(401, "Login required")
    mine = [e for e in _load_index() if e.get("user_id") == user_id]
    mine.sort(key=lambda e: e.get("last_used_at") or e.get("created_at") or "", reverse=True)
    return {"history": mine}


@router.get("/history/load/{session_id}")
async def load_history(session_id: str, authorization: str | None = Header(None)):
    """Reload a previously generated PPT into the viewer."""
    if re.search(r'[/\\.]\.', session_id):
        raise HTTPException(400, "Invalid session id")
    user_id = _user_id_from_token(authorization)
    if not user_id:
        raise HTTPException(401, "Login required")
    entry = next((e for e in _load_index() if e.get("session_id") == session_id), None)
    if not entry or entry.get("user_id") != user_id:
        raise HTTPException(404, "Presentation not found")
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
    title = entry.get("title") or (slides[0]["title"] if slides else "Presentation")
    _slide_store[session_id]  = slides
    _ppt_titles[session_id]   = title
    _current_slide[session_id] = 0

    global _latest_upload_sid
    _latest_upload_sid = session_id
    _touch_index_entry(session_id)

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
                from backend.services.ppt_template_builder import extract_slide_data, _parse_source
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

