"""
uno_client.py
=============
FastAPI-side client for the UNO IPC worker.

This module runs inside the main FastAPI (Python 3.11) process.
It manages a persistent Python 3.12 uno_worker subprocess and sends
JSON-RPC messages to it over a Unix domain socket.
"""

from __future__ import annotations
import asyncio
import base64
import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time

logger = logging.getLogger("uno_client")

IPC_SOCKET_PATH = "/tmp/lo_uno_worker.sock"
WORKER_PYTHON    = "/opt/homebrew/bin/python3.12"
WORKER_SCRIPT    = os.path.join(
    os.path.dirname(__file__), "uno_worker.py"
)

_PYUNO_PATHS = [
    "/Applications/LibreOffice.app/Contents/Frameworks",
    "/Applications/LibreOffice.app/Contents/Resources",
]


class UNOWorkerClient:
    """
    Thread-safe client for the UNO IPC worker process.
    Maintains a persistent connection to the Unix socket.
    Auto-reconnects on socket failure.
    """

    def __init__(
        self,
        ipc_path: str = IPC_SOCKET_PATH,
        timeout: float = 15.0,
    ):
        self.ipc_path = ipc_path
        self.timeout = timeout
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._buf = b""
        self._req_id = 0
        self._worker_proc: subprocess.Popen | None = None

    # ------------------------------------------------------------------
    # Subprocess lifecycle
    # ------------------------------------------------------------------

    def start_worker(self):
        """Launch the Python 3.12 uno_worker.py subprocess."""
        if self._worker_proc and self._worker_proc.poll() is None:
            logger.info("UNO worker subprocess is already running.")
            return

        python_exec = shutil.which("python3.12") or WORKER_PYTHON
        if not os.path.exists(python_exec):
            raise RuntimeError(
                f"python3.12 not found at '{python_exec}'. "
                "Run: brew install python@3.12"
            )

        env = os.environ.copy()
        existing_pp = env.get("PYTHONPATH", "")
        extra = ":".join(_PYUNO_PATHS)
        env["PYTHONPATH"] = f"{extra}:{existing_pp}" if existing_pp else extra

        cmd = [python_exec, WORKER_SCRIPT, "--ipc-path", self.ipc_path]
        logger.info(f"Launching UNO worker: {' '.join(cmd)}")
        self._worker_proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
        )

        # Wait for the socket file to appear
        for _ in range(30):
            if os.path.exists(self.ipc_path):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("UNO worker process did not create IPC socket in time.")

        logger.info("UNO worker is ready.")

    def stop_worker(self):
        """Stop the uno_worker subprocess."""
        if self._worker_proc:
            try:
                self._worker_proc.terminate()
                self._worker_proc.wait(timeout=5.0)
            except Exception:
                try:
                    self._worker_proc.kill()
                except Exception:
                    pass
            self._worker_proc = None

    def _ensure_connected(self):
        if self._sock is not None:
            return
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self.ipc_path)
        self._sock = s
        self._buf = b""

    def _disconnect(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
            self._buf = b""

    # ------------------------------------------------------------------
    # RPC call
    # ------------------------------------------------------------------

    def call(self, method: str, **params) -> dict:
        """
        Synchronous JSON-RPC call. Thread-safe.
        Raises RuntimeError on RPC error, ConnectionError on socket failure.
        """
        with self._lock:
            self._req_id += 1
            req_id = self._req_id
            payload = json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            }).encode() + b"\n"

            for attempt in range(3):
                try:
                    self._ensure_connected()
                    self._sock.sendall(payload)

                    # Read until newline (one response per request)
                    while b"\n" not in self._buf:
                        chunk = self._sock.recv(131072)
                        if not chunk:
                            raise ConnectionError("Worker closed connection.")
                        self._buf += chunk

                    line, self._buf = self._buf.split(b"\n", 1)
                    resp = json.loads(line)

                    if "error" in resp:
                        raise RuntimeError(
                            f"UNO RPC error in '{method}': "
                            f"{resp['error'].get('message', resp['error'])}"
                        )
                    return resp["result"]

                except (ConnectionError, OSError, BrokenPipeError) as e:
                    logger.warning(f"Socket error (attempt {attempt + 1}): {e}. Reconnecting…")
                    self._disconnect()
                    # Restart worker if it crashed
                    if self._worker_proc and self._worker_proc.poll() is not None:
                        logger.warning("UNO worker subprocess died. Restarting…")
                        self.start_worker()
                    time.sleep(0.5)

            raise ConnectionError(f"Could not reach UNO worker after 3 attempts for method '{method}'")

    # ------------------------------------------------------------------
    # Async wrapper (for use inside FastAPI async handlers)
    # ------------------------------------------------------------------

    async def acall(self, method: str, **params) -> dict:
        """Async wrapper: runs `call` in a threadpool so it doesn't block the event loop."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.call(method, **params))

    # ------------------------------------------------------------------
    # Convenience API (mirrors LibreOfficeDocumentService methods)
    # ------------------------------------------------------------------

    def connect_session(self, session_id: str, port: int = 2002) -> dict:
        return self.call("connect", session_id=session_id, port=port)

    def open_document(self, session_id: str, path: str) -> dict:
        return self.call("open_document", session_id=session_id, path=path)

    def create_blank_presentation(self, session_id: str) -> dict:
        return self.call("create_blank_presentation", session_id=session_id)

    def save(self, session_id: str, path: str | None = None) -> dict:
        return self.call("save", session_id=session_id, path=path)

    def close(self, session_id: str) -> dict:
        return self.call("close", session_id=session_id)

    def get_slide_count(self, session_id: str) -> int:
        return self.call("get_slide_count", session_id=session_id)["count"]

    def add_slide(self, session_id: str, index: int | None = None, layout_index: int = 1) -> dict:
        return self.call("add_slide", session_id=session_id, index=index, layout_index=layout_index)

    def delete_slide(self, session_id: str, index: int) -> dict:
        return self.call("delete_slide", session_id=session_id, index=index)

    def duplicate_slide(self, session_id: str, index: int) -> dict:
        return self.call("duplicate_slide", session_id=session_id, index=index)

    def reorder_slides(self, session_id: str, new_order: list[int]) -> dict:
        return self.call("reorder_slides", session_id=session_id, new_order=new_order)

    def set_slide_title(self, session_id: str, slide_index: int, title: str) -> dict:
        return self.call("set_slide_title", session_id=session_id, slide_index=slide_index, title=title)

    def set_slide_content(self, session_id: str, slide_index: int, lines: list[str]) -> dict:
        return self.call("set_slide_content", session_id=session_id, slide_index=slide_index, lines=lines)

    def edit_text_in_shape(self, session_id: str, slide_index: int, shape_id: int, new_text: str) -> dict:
        return self.call("edit_text_in_shape", session_id=session_id,
                         slide_index=slide_index, shape_id=shape_id, new_text=new_text)

    def apply_font_to_shape(
        self, session_id: str, slide_index: int, shape_id: int,
        font_name: str | None = None, font_size_pt: float | None = None,
        bold: bool | None = None, italic: bool | None = None,
        color_hex: str | None = None,
    ) -> dict:
        return self.call(
            "apply_font_to_shape", session_id=session_id,
            slide_index=slide_index, shape_id=shape_id,
            font_name=font_name, font_size_pt=font_size_pt,
            bold=bold, italic=italic, color_hex=color_hex,
        )

    def apply_background_color(self, session_id: str, slide_index: int, color_hex: str) -> dict:
        return self.call("apply_background_color", session_id=session_id,
                         slide_index=slide_index, color_hex=color_hex)

    def add_image(
        self, session_id: str, slide_index: int, image_path: str,
        x_cm: float = 2.0, y_cm: float = 5.0,
        width_cm: float = 10.0, height_cm: float = 7.0,
    ) -> dict:
        return self.call("add_image", session_id=session_id, slide_index=slide_index,
                         image_path=image_path, x_cm=x_cm, y_cm=y_cm,
                         width_cm=width_cm, height_cm=height_cm)

    def get_slide_thumbnail(self, session_id: str, index: int,
                            width_px: int = 1280, height_px: int = 720) -> bytes:
        result = self.call("get_slide_thumbnail", session_id=session_id,
                           index=index, width_px=width_px, height_px=height_px)
        return base64.b64decode(result["png_b64"])

    def get_all_thumbnails(self, session_id: str,
                           width_px: int = 1280, height_px: int = 720) -> list[bytes]:
        result = self.call("get_all_thumbnails", session_id=session_id,
                           width_px=width_px, height_px=height_px)
        return [base64.b64decode(t) for t in result["thumbnails_b64"]]

    def list_available_layouts(self, session_id: str) -> list[str]:
        return self.call("list_available_layouts", session_id=session_id)["layouts"]

    def apply_master_slide(self, session_id: str, slide_index: int, master_name: str) -> dict:
        return self.call("apply_master_slide", session_id=session_id,
                         slide_index=slide_index, master_name=master_name)

    def export(self, session_id: str, output_path: str, fmt: str = "pptx") -> dict:
        return self.call("export", session_id=session_id, output_path=output_path, fmt=fmt)

    def run_macro(self, session_id: str, macro_name: str, args: list | None = None) -> dict:
        return self.call("run_macro", session_id=session_id, macro_name=macro_name, args=args)

    def poll_events(self) -> list[dict]:
        """Drain document-change events from the worker's queue."""
        return self.call("poll_events")["events"]


# ---------------------------------------------------------------------------
# Module-level singleton (one worker, many sessions)
# ---------------------------------------------------------------------------

_client: UNOWorkerClient | None = None


def get_uno_client() -> UNOWorkerClient:
    """Return the module-level singleton UNOWorkerClient (lazily initialized)."""
    global _client
    if _client is None:
        _client = UNOWorkerClient()
    return _client
