"""
ppt_uno.py
==========
FastAPI router exposing LibreOffice UNO document operations.

All endpoints delegate to the UNO worker subprocess via uno_client.
WebSocket-push for real-time slide updates is handled by the
`/ppt/uno/events` SSE stream which drains the worker's change-event queue.

Mount in main.py:
    from backend.api.ppt_uno import router as ppt_uno_router
    app.include_router(ppt_uno_router, prefix="/ppt/uno", tags=["LibreOffice UNO"])
"""

from __future__ import annotations
import asyncio
import base64
import logging
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from backend.services.libreoffice import get_uno_client
from backend.queues.bus import bus

logger = logging.getLogger("ppt_uno")
router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SessionCmd(BaseModel):
    session_id: str
    uno_port: int = 2002


class OpenDocumentCmd(BaseModel):
    session_id: str
    path: str


class AddSlideUNOCmd(BaseModel):
    session_id: str
    index: int | None = None
    layout_index: int = Field(default=1, description="0=blank, 1=title+content, 20=title only")


class DeleteSlideUNOCmd(BaseModel):
    session_id: str
    index: int


class DuplicateSlideCmd(BaseModel):
    session_id: str
    index: int


class ReorderSlidesCmd(BaseModel):
    session_id: str
    new_order: list[int]


class SetTitleCmd(BaseModel):
    session_id: str
    slide_index: int
    title: str


class SetContentCmd(BaseModel):
    session_id: str
    slide_index: int
    lines: list[str]


class EditShapeTextCmd(BaseModel):
    session_id: str
    slide_index: int
    shape_id: int
    new_text: str


class ApplyFontCmd(BaseModel):
    session_id: str
    slide_index: int
    shape_id: int
    font_name: str | None = None
    font_size_pt: float | None = None
    bold: bool | None = None
    italic: bool | None = None
    color_hex: str | None = None


class ApplyBackgroundCmd(BaseModel):
    session_id: str
    slide_index: int
    color_hex: str


class AddImageCmd(BaseModel):
    session_id: str
    slide_index: int
    image_path: str
    x_cm: float = 2.0
    y_cm: float = 5.0
    width_cm: float = 10.0
    height_cm: float = 7.0


class ApplyMasterCmd(BaseModel):
    session_id: str
    slide_index: int
    master_name: str


class ExportCmd(BaseModel):
    session_id: str
    output_path: str
    fmt: str = "pptx"


class MacroCmd(BaseModel):
    session_id: str
    macro_name: str
    args: list | None = None


class SaveCmd(BaseModel):
    session_id: str
    path: str | None = None


# ---------------------------------------------------------------------------
# Helper: emit slide-state update over existing WebSocket channel
# ---------------------------------------------------------------------------

async def _emit_slide_state(session_id: str, action: str = "reload"):
    """
    After a UNO mutation, get all thumbnails and push a WebSocket reload event.
    This replaces the old manual _slide_store update pattern.
    """
    try:
        client = get_uno_client()
        thumbnails = await client.acall(
            "get_all_thumbnails", session_id=session_id, width_px=1280, height_px=720
        )
        slides_payload = [
            {"index": i, "img_b64": t}
            for i, t in enumerate(thumbnails.get("thumbnails_b64", []))
        ]
        await bus.emit_event(
            "ppt_command",
            {
                "action": action,
                "slides": slides_payload,
                "filename": f"Presentation ({len(slides_payload)} slides)",
            },
            session_id,
        )
    except Exception as e:
        logger.warning(f"_emit_slide_state failed for session '{session_id}': {e}")


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@router.post("/connect")
async def uno_connect(cmd: SessionCmd):
    """Connect UNO worker to the running LibreOffice daemon for this session."""
    try:
        client = get_uno_client()
        result = await client.acall("connect", session_id=cmd.session_id, port=cmd.uno_port)
        return result
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/open_document")
async def uno_open_document(cmd: OpenDocumentCmd):
    """Open an existing .pptx/.odp file for the session."""
    if not os.path.exists(cmd.path):
        raise HTTPException(status_code=404, detail=f"File not found: {cmd.path}")
    try:
        client = get_uno_client()
        result = await client.acall("open_document", session_id=cmd.session_id, path=cmd.path)
        await _emit_slide_state(cmd.session_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create_blank")
async def uno_create_blank(cmd: SessionCmd):
    """Create a new blank presentation for the session."""
    try:
        client = get_uno_client()
        result = await client.acall("create_blank_presentation", session_id=cmd.session_id)
        await _emit_slide_state(cmd.session_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/save")
async def uno_save(cmd: SaveCmd):
    """Save the currently open document."""
    try:
        client = get_uno_client()
        return await client.acall("save", session_id=cmd.session_id, path=cmd.path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/close")
async def uno_close(cmd: SessionCmd):
    """Close the currently open document and release the session."""
    try:
        client = get_uno_client()
        return await client.acall("close", session_id=cmd.session_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Slide structure
# ---------------------------------------------------------------------------

@router.post("/add_slide")
async def uno_add_slide(cmd: AddSlideUNOCmd):
    """Add a new slide at optional index (defaults to end)."""
    try:
        client = get_uno_client()
        result = await client.acall(
            "add_slide",
            session_id=cmd.session_id,
            index=cmd.index,
            layout_index=cmd.layout_index,
        )
        await _emit_slide_state(cmd.session_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/delete_slide")
async def uno_delete_slide(cmd: DeleteSlideUNOCmd):
    """Delete the slide at the given zero-based index."""
    try:
        client = get_uno_client()
        result = await client.acall("delete_slide", session_id=cmd.session_id, index=cmd.index)
        await _emit_slide_state(cmd.session_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/duplicate_slide")
async def uno_duplicate_slide(cmd: DuplicateSlideCmd):
    """Duplicate the slide at index; insert after it."""
    try:
        client = get_uno_client()
        result = await client.acall("duplicate_slide", session_id=cmd.session_id, index=cmd.index)
        await _emit_slide_state(cmd.session_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reorder_slides")
async def uno_reorder_slides(cmd: ReorderSlidesCmd):
    """Reorder slides by providing a complete permutation of current indices."""
    try:
        client = get_uno_client()
        result = await client.acall("reorder_slides", session_id=cmd.session_id, new_order=cmd.new_order)
        await _emit_slide_state(cmd.session_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Content editing
# ---------------------------------------------------------------------------

@router.post("/set_title")
async def uno_set_title(cmd: SetTitleCmd):
    """Set the title text of a slide's title shape."""
    try:
        client = get_uno_client()
        result = await client.acall(
            "set_slide_title",
            session_id=cmd.session_id,
            slide_index=cmd.slide_index,
            title=cmd.title,
        )
        await _emit_slide_state(cmd.session_id, action="reload")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/set_content")
async def uno_set_content(cmd: SetContentCmd):
    """Replace the body/bullet content of a slide with a list of text lines."""
    try:
        client = get_uno_client()
        result = await client.acall(
            "set_slide_content",
            session_id=cmd.session_id,
            slide_index=cmd.slide_index,
            lines=cmd.lines,
        )
        await _emit_slide_state(cmd.session_id, action="reload")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/edit_shape_text")
async def uno_edit_shape_text(cmd: EditShapeTextCmd):
    """Directly edit the text of any shape by shape_id on a given slide."""
    try:
        client = get_uno_client()
        result = await client.acall(
            "edit_text_in_shape",
            session_id=cmd.session_id,
            slide_index=cmd.slide_index,
            shape_id=cmd.shape_id,
            new_text=cmd.new_text,
        )
        await _emit_slide_state(cmd.session_id, action="reload")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Fonts & styles
# ---------------------------------------------------------------------------

@router.post("/apply_font")
async def uno_apply_font(cmd: ApplyFontCmd):
    """Apply font name, size, bold, italic, and color to a shape's text."""
    try:
        client = get_uno_client()
        result = await client.acall(
            "apply_font_to_shape",
            session_id=cmd.session_id,
            slide_index=cmd.slide_index,
            shape_id=cmd.shape_id,
            font_name=cmd.font_name,
            font_size_pt=cmd.font_size_pt,
            bold=cmd.bold,
            italic=cmd.italic,
            color_hex=cmd.color_hex,
        )
        await _emit_slide_state(cmd.session_id, action="reload")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/apply_background")
async def uno_apply_background(cmd: ApplyBackgroundCmd):
    """Set a solid background fill color for a slide."""
    try:
        client = get_uno_client()
        result = await client.acall(
            "apply_background_color",
            session_id=cmd.session_id,
            slide_index=cmd.slide_index,
            color_hex=cmd.color_hex,
        )
        await _emit_slide_state(cmd.session_id, action="reload")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

@router.post("/add_image")
async def uno_add_image(cmd: AddImageCmd):
    """Insert a PNG/JPG image onto a slide at the specified position (in cm)."""
    if not os.path.exists(cmd.image_path):
        raise HTTPException(status_code=404, detail=f"Image not found: {cmd.image_path}")
    try:
        client = get_uno_client()
        result = await client.acall(
            "add_image",
            session_id=cmd.session_id,
            slide_index=cmd.slide_index,
            image_path=cmd.image_path,
            x_cm=cmd.x_cm,
            y_cm=cmd.y_cm,
            width_cm=cmd.width_cm,
            height_cm=cmd.height_cm,
        )
        await _emit_slide_state(cmd.session_id, action="reload")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------

@router.get("/thumbnail/{session_id}/{slide_index}")
async def uno_thumbnail(session_id: str, slide_index: int, width: int = 1280, height: int = 720):
    """Stream a single slide thumbnail as PNG."""
    try:
        client = get_uno_client()
        result = await client.acall(
            "get_slide_thumbnail",
            session_id=session_id,
            index=slide_index,
            width_px=width,
            height_px=height,
        )
        png_bytes = base64.b64decode(result["png_b64"])
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/thumbnails/{session_id}")
async def uno_all_thumbnails(session_id: str, width: int = 1280, height: int = 720):
    """Return all slide thumbnails as a JSON array of base64-encoded PNGs."""
    try:
        client = get_uno_client()
        result = await client.acall(
            "get_all_thumbnails",
            session_id=session_id,
            width_px=width,
            height_px=height,
        )
        return {"thumbnails": result["thumbnails_b64"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Master slides
# ---------------------------------------------------------------------------

@router.get("/layouts/{session_id}")
async def uno_list_layouts(session_id: str):
    """List available master slide names for the session document."""
    try:
        client = get_uno_client()
        result = await client.acall("list_available_layouts", session_id=session_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/apply_master")
async def uno_apply_master(cmd: ApplyMasterCmd):
    """Apply a named master slide to a specific slide."""
    try:
        client = get_uno_client()
        result = await client.acall(
            "apply_master_slide",
            session_id=cmd.session_id,
            slide_index=cmd.slide_index,
            master_name=cmd.master_name,
        )
        await _emit_slide_state(cmd.session_id, action="reload")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Export & Macros
# ---------------------------------------------------------------------------

@router.post("/export")
async def uno_export(cmd: ExportCmd):
    """Export the document to pptx, pdf, or odp."""
    try:
        client = get_uno_client()
        return await client.acall(
            "export", session_id=cmd.session_id,
            output_path=cmd.output_path, fmt=cmd.fmt,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run_macro")
async def uno_run_macro(cmd: MacroCmd):
    """Execute a LibreOffice Basic macro by fully-qualified name."""
    try:
        client = get_uno_client()
        return await client.acall(
            "run_macro", session_id=cmd.session_id,
            macro_name=cmd.macro_name, args=cmd.args,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Real-time event stream (Server-Sent Events)
# Drains the worker's modify-event queue and pushes updates over SSE.
# The React frontend can also receive these via the existing WebSocket bus.
# ---------------------------------------------------------------------------

@router.get("/events/{session_id}")
async def uno_events_stream(session_id: str, request: Request):
    """
    SSE stream of document-change events from the UNO modify listener.
    Each event triggers a full slide-state push over the WebSocket bus.
    """
    async def event_generator():
        client = get_uno_client()
        yield "data: {\"type\": \"connected\"}\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                events = client.poll_events()
                for ev in events:
                    if ev.get("session_id") == session_id:
                        await _emit_slide_state(session_id)
                        yield f"data: {{\"type\": \"modified\"}}\n\n"
            except Exception as e:
                yield f"data: {{\"type\": \"error\", \"message\": \"{str(e)}\"}}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
