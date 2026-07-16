#!/usr/bin/env python3
"""
uno_worker.py
=============
Standalone JSON-RPC process that wraps LibreOfficeDocumentService.

Launch ONLY via Python 3.12 (with pyuno on PYTHONPATH):
    PYTHONPATH=/Applications/LibreOffice.app/Contents/Frameworks:\
               /Applications/LibreOffice.app/Contents/Resources \
    /opt/homebrew/bin/python3.12 uno_worker.py \
        --port 2002 --ipc-path /tmp/lo_worker.sock

It accepts newline-delimited JSON-RPC 2.0 requests on a Unix domain socket
and returns newline-delimited JSON-RPC 2.0 responses.
"""

from __future__ import annotations
import argparse
import base64
import json
import logging
import os
import queue
import socket
import sys
import threading
import traceback

# Ensure pyuno.so can be found (when launched by the FastAPI subprocess manager)
sys.path.insert(0, "/Applications/LibreOffice.app/Contents/Frameworks")
sys.path.insert(0, "/Applications/LibreOffice.app/Contents/Resources")

from uno_bridge import LibreOfficeDocumentService  # noqa

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [uno_worker] %(levelname)s %(message)s",
)
logger = logging.getLogger("uno_worker")

# ---------------------------------------------------------------------------
# Session registry: session_id -> LibreOfficeDocumentService
# ---------------------------------------------------------------------------
_sessions: dict[str, LibreOfficeDocumentService] = {}
_sessions_lock = threading.Lock()

# Change-event queue: each item is {"session_id": ..., "event": "modified"}
# The FastAPI process drains this via the /ppt/events SSE or WebSocket.
_event_queue: queue.Queue = queue.Queue(maxsize=500)


def _get_or_error(session_id: str) -> LibreOfficeDocumentService:
    with _sessions_lock:
        svc = _sessions.get(session_id)
    if svc is None:
        raise KeyError(f"No open document for session_id='{session_id}'")
    return svc


# ---------------------------------------------------------------------------
# RPC method dispatch table
# ---------------------------------------------------------------------------

def rpc_connect(params: dict) -> dict:
    port = params.get("port", 2002)
    session_id = params["session_id"]
    with _sessions_lock:
        if session_id in _sessions:
            return {"status": "already_connected"}
        svc = LibreOfficeDocumentService(port=port)
    svc.connect()
    with _sessions_lock:
        _sessions[session_id] = svc
    return {"status": "connected"}


def rpc_open_document(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    svc.open_document(params["path"])
    _register_modify_listener(params["session_id"], svc)
    return {"status": "opened"}


def rpc_create_blank_presentation(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    svc.create_blank_presentation()
    _register_modify_listener(params["session_id"], svc)
    return {"status": "created"}


def rpc_save(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    svc.save(params.get("path"))
    return {"status": "saved"}


def rpc_close(params: dict) -> dict:
    session_id = params["session_id"]
    svc = _get_or_error(session_id)
    svc.close()
    with _sessions_lock:
        _sessions.pop(session_id, None)
    return {"status": "closed"}


def rpc_get_slide_count(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    return {"count": svc.get_slide_count()}


def rpc_add_slide(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    idx = svc.add_slide(
        index=params.get("index"),
        layout_index=params.get("layout_index", 1),
    )
    return {"index": idx, "slide_count": svc.get_slide_count()}


def rpc_delete_slide(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    svc.delete_slide(params["index"])
    return {"slide_count": svc.get_slide_count()}


def rpc_duplicate_slide(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    new_idx = svc.duplicate_slide(params["index"])
    return {"new_index": new_idx, "slide_count": svc.get_slide_count()}


def rpc_reorder_slides(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    svc.reorder_slides(params["new_order"])
    return {"status": "reordered"}


def rpc_set_slide_title(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    svc.set_slide_title(params["slide_index"], params["title"])
    return {"status": "ok"}


def rpc_set_slide_content(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    svc.set_slide_content(params["slide_index"], params["lines"])
    return {"status": "ok"}


def rpc_edit_text_in_shape(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    svc.edit_text_in_shape(params["slide_index"], params["shape_id"], params["new_text"])
    return {"status": "ok"}


def rpc_apply_font_to_shape(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    svc.apply_font_to_shape(
        params["slide_index"],
        params["shape_id"],
        font_name=params.get("font_name"),
        font_size_pt=params.get("font_size_pt"),
        bold=params.get("bold"),
        italic=params.get("italic"),
        color_hex=params.get("color_hex"),
    )
    return {"status": "ok"}


def rpc_apply_background_color(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    svc.apply_background_color(params["slide_index"], params["color_hex"])
    return {"status": "ok"}


def rpc_add_image(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    shape_idx = svc.add_image(
        params["slide_index"],
        params["image_path"],
        x_cm=params.get("x_cm", 2.0),
        y_cm=params.get("y_cm", 5.0),
        width_cm=params.get("width_cm", 10.0),
        height_cm=params.get("height_cm", 7.0),
    )
    return {"shape_index": shape_idx}


def rpc_get_slide_thumbnail(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    png_bytes = svc.get_slide_thumbnail(
        params["index"],
        width_px=params.get("width_px", 1280),
        height_px=params.get("height_px", 720),
    )
    return {"png_b64": base64.b64encode(png_bytes).decode()}


def rpc_get_all_thumbnails(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    thumbnails = svc.get_all_thumbnails(
        width_px=params.get("width_px", 1280),
        height_px=params.get("height_px", 720),
    )
    return {"thumbnails_b64": [base64.b64encode(t).decode() for t in thumbnails]}


def rpc_list_available_layouts(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    return {"layouts": svc.list_available_layouts()}


def rpc_apply_master_slide(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    svc.apply_master_slide(params["slide_index"], params["master_name"])
    return {"status": "ok"}


def rpc_export(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    svc.export(params["output_path"], fmt=params.get("fmt", "pptx"))
    return {"status": "exported", "path": params["output_path"]}


def rpc_run_macro(params: dict) -> dict:
    svc = _get_or_error(params["session_id"])
    result = svc.run_macro(params["macro_name"], params.get("args"))
    return {"result": result}


def rpc_poll_events(params: dict) -> dict:
    """Drain pending document-change events (called by the FastAPI bridge poller)."""
    events = []
    try:
        while True:
            ev = _event_queue.get_nowait()
            events.append(ev)
    except queue.Empty:
        pass
    return {"events": events}


DISPATCH = {
    "connect":                    rpc_connect,
    "open_document":              rpc_open_document,
    "create_blank_presentation":  rpc_create_blank_presentation,
    "save":                       rpc_save,
    "close":                      rpc_close,
    "get_slide_count":            rpc_get_slide_count,
    "add_slide":                  rpc_add_slide,
    "delete_slide":               rpc_delete_slide,
    "duplicate_slide":            rpc_duplicate_slide,
    "reorder_slides":             rpc_reorder_slides,
    "set_slide_title":            rpc_set_slide_title,
    "set_slide_content":          rpc_set_slide_content,
    "edit_text_in_shape":         rpc_edit_text_in_shape,
    "apply_font_to_shape":        rpc_apply_font_to_shape,
    "apply_background_color":     rpc_apply_background_color,
    "add_image":                  rpc_add_image,
    "get_slide_thumbnail":        rpc_get_slide_thumbnail,
    "get_all_thumbnails":         rpc_get_all_thumbnails,
    "list_available_layouts":     rpc_list_available_layouts,
    "apply_master_slide":         rpc_apply_master_slide,
    "export":                     rpc_export,
    "run_macro":                  rpc_run_macro,
    "poll_events":                rpc_poll_events,
}


def _register_modify_listener(session_id: str, svc: LibreOfficeDocumentService):
    """Wire a UNO ModifyListener that pushes events into _event_queue."""
    def _on_modified(event):
        try:
            _event_queue.put_nowait({"session_id": session_id, "event": "modified"})
        except queue.Full:
            pass

    try:
        svc.add_modify_listener(_on_modified)
    except Exception as e:
        logger.warning(f"Could not register modify listener for session '{session_id}': {e}")


# ---------------------------------------------------------------------------
# JSON-RPC request handler
# ---------------------------------------------------------------------------

def handle_request(raw: bytes) -> bytes:
    try:
        req = json.loads(raw.decode())
    except json.JSONDecodeError as e:
        return json.dumps({
            "jsonrpc": "2.0", "id": None,
            "error": {"code": -32700, "message": f"Parse error: {e}"},
        }).encode() + b"\n"

    req_id = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {})

    handler = DISPATCH.get(method)
    if handler is None:
        return json.dumps({
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: '{method}'"},
        }).encode() + b"\n"

    try:
        result = handler(params)
        return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}).encode() + b"\n"
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Error in method '{method}': {e}\n{tb}")
        return json.dumps({
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32000, "message": str(e), "data": tb},
        }).encode() + b"\n"


# ---------------------------------------------------------------------------
# Unix socket server
# ---------------------------------------------------------------------------

def serve(ipc_path: str):
    if os.path.exists(ipc_path):
        os.unlink(ipc_path)

    server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_sock.bind(ipc_path)
    server_sock.listen(10)
    os.chmod(ipc_path, 0o600)

    logger.info(f"UNO worker listening on Unix socket: {ipc_path}")

    def _handle_client(conn: socket.socket):
        buf = b""
        try:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        resp = handle_request(line)
                        conn.sendall(resp)
        except Exception as e:
            logger.debug(f"Client connection closed: {e}")
        finally:
            conn.close()

    while True:
        try:
            conn, _ = server_sock.accept()
            t = threading.Thread(target=_handle_client, args=(conn,), daemon=True)
            t.start()
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Accept error: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LibreOffice UNO JSON-RPC IPC worker")
    parser.add_argument("--ipc-path", default="/tmp/lo_uno_worker.sock",
                        help="Unix socket path for IPC (default: /tmp/lo_uno_worker.sock)")
    args = parser.parse_args()
    serve(args.ipc_path)
