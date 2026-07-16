"""
test_libreoffice_uno.py
=======================
Progressive integration tests for the LibreOffice UNO service layer.

Tests are split into 4 stages so you can diagnose exactly which layer fails:
  Stage 1 — Daemon:    Is soffice running and accepting socket connections?
  Stage 2 — Worker:    Can the Python 3.12 uno_worker subprocess start?
  Stage 3 — Client:    Can the FastAPI Python 3.11 client talk to the worker?
  Stage 4 — Document:  Full end-to-end: create doc, add slide, thumbnail, export.

Run from the PILOT project root:
    PYTHONPATH=. python3 backend/tests/test_libreoffice_uno.py

Or run a specific stage:
    PYTHONPATH=. python3 backend/tests/test_libreoffice_uno.py --stage 3
"""

import argparse
import base64
import os
import socket
import subprocess
import sys
import tempfile
import time
import threading
import json

PILOT_ROOT  = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UNO_PORT    = 2002
IPC_SOCKET  = "/tmp/lo_uno_test.sock"
SESSION_ID  = "test_session"
PYTHON_312  = "/opt/homebrew/bin/python3.12"
SOFFICE     = "/opt/homebrew/bin/soffice"
WORKER_SCRIPT = os.path.join(PILOT_ROOT, "backend", "services", "libreoffice", "uno_worker.py")
PYUNO_PATHS = [
    "/Applications/LibreOffice.app/Contents/Frameworks",
    "/Applications/LibreOffice.app/Contents/Resources",
]

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
INFO = "\033[94m→\033[0m"


def section(title: str):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def ok(msg: str):   print(f"  {PASS}  {msg}")
def err(msg: str):  print(f"  {FAIL}  {msg}")
def info(msg: str): print(f"  {INFO}  {msg}")


# ─────────────────────────────────────────────────────────────────
# STAGE 1: LibreOffice Daemon (soffice socket)
# ─────────────────────────────────────────────────────────────────

def stage1_daemon():
    section("Stage 1 — LibreOffice Daemon (soffice socket)")

    # Check soffice binary
    soffice_path = None
    for p in [SOFFICE, "/Applications/LibreOffice.app/Contents/MacOS/soffice", "/usr/local/bin/soffice"]:
        if os.path.exists(p):
            soffice_path = p
            break
    if not soffice_path:
        err("soffice binary not found. Install LibreOffice.")
        return None

    ok(f"soffice found at: {soffice_path}")

    # Check if already listening
    def is_listening():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            s.connect(("127.0.0.1", UNO_PORT))
            s.close()
            return True
        except Exception:
            return False

    if is_listening():
        ok(f"soffice already listening on port {UNO_PORT}")
        return soffice_path

    # Launch it
    info(f"Starting headless soffice on port {UNO_PORT}...")
    profile_dir = os.path.abspath("data/ppt/soffice_test_profile")
    os.makedirs(profile_dir, exist_ok=True)

    proc = subprocess.Popen(
        [
            soffice_path,
            "--headless", "--invisible", "--nocrashreport",
            "--nodefault", "--norestore", "--nofirststartwizard", "--nologo",
            f"-env:UserInstallation=file://{profile_dir}",
            f"--accept=socket,host=localhost,port={UNO_PORT};urp;",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for i in range(20):
        if is_listening():
            ok(f"soffice is now listening on port {UNO_PORT} (PID {proc.pid})")
            return soffice_path
        time.sleep(0.5)

    proc.terminate()
    err("soffice process started but failed to bind to the UNO socket port in time.")
    return None


# ─────────────────────────────────────────────────────────────────
# STAGE 2: uno_worker subprocess (Python 3.12 + pyuno)
# ─────────────────────────────────────────────────────────────────

_worker_proc = None

def stage2_worker():
    global _worker_proc
    section("Stage 2 — UNO Worker subprocess (Python 3.12 + pyuno)")

    # Check python3.12
    python_path = None
    for p in [PYTHON_312, "/usr/local/bin/python3.12"]:
        if os.path.exists(p):
            python_path = p
            break
    if not python_path:
        err("python3.12 not found. Run: brew install python@3.12")
        return False
    ok(f"python3.12 found at: {python_path}")

    # Check worker script
    if not os.path.exists(WORKER_SCRIPT):
        err(f"uno_worker.py not found at: {WORKER_SCRIPT}")
        return False
    ok(f"uno_worker.py found at: {WORKER_SCRIPT}")

    # Clean up old socket
    if os.path.exists(IPC_SOCKET):
        os.unlink(IPC_SOCKET)

    env = os.environ.copy()
    env["PYTHONPATH"] = ":".join(PYUNO_PATHS) + ":" + env.get("PYTHONPATH", "")

    info("Launching uno_worker.py subprocess...")
    _worker_proc = subprocess.Popen(
        [python_path, WORKER_SCRIPT, "--ipc-path", IPC_SOCKET],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for socket to appear
    for i in range(20):
        if os.path.exists(IPC_SOCKET):
            ok(f"IPC socket created: {IPC_SOCKET}")
            break
        if _worker_proc.poll() is not None:
            stdout, stderr = _worker_proc.communicate()
            err(f"uno_worker.py exited prematurely.")
            print(f"    STDOUT: {stdout.decode()[:500]}")
            print(f"    STDERR: {stderr.decode()[:500]}")
            return False
        time.sleep(0.3)
    else:
        err("uno_worker.py did not create IPC socket in time.")
        _worker_proc.terminate()
        return False

    ok("UNO worker subprocess is running.")
    return True


# ─────────────────────────────────────────────────────────────────
# STAGE 3: Client RPC (Python 3.11 → IPC socket → worker)
# ─────────────────────────────────────────────────────────────────

_req_id = 0

def rpc(method: str, **params) -> dict:
    global _req_id
    _req_id += 1
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": _req_id,
        "method": method,
        "params": params,
    }).encode() + b"\n"

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(15.0)
    s.connect(IPC_SOCKET)
    s.sendall(payload)

    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(131072)
        if not chunk:
            raise ConnectionError("Worker closed connection")
        buf += chunk
    s.close()

    line = buf.split(b"\n")[0]
    resp = json.loads(line)
    if "error" in resp:
        raise RuntimeError(f"RPC error: {resp['error']}")
    return resp["result"]


def stage3_client():
    section("Stage 3 — Client RPC (Python 3.11 → IPC socket → worker)")

    if not os.path.exists(IPC_SOCKET):
        err(f"IPC socket not found at {IPC_SOCKET}. Did Stage 2 pass?")
        return False

    # connect
    info("Sending 'connect' RPC...")
    try:
        result = rpc("connect", session_id=SESSION_ID, port=UNO_PORT)
        ok(f"connect → {result}")
    except Exception as e:
        err(f"connect failed: {e}")
        return False

    # get_slide_count (before any document open — should fail cleanly)
    info("Sending 'get_slide_count' before opening document (expect error)...")
    try:
        result = rpc("get_slide_count", session_id=SESSION_ID)
        info(f"get_slide_count returned: {result} (document may already be open)")
    except RuntimeError as e:
        ok(f"Correct error before document open: {e}")

    ok("Client RPC is functional.")
    return True


# ─────────────────────────────────────────────────────────────────
# STAGE 4: End-to-end document operations
# ─────────────────────────────────────────────────────────────────

def stage4_document():
    section("Stage 4 — End-to-end document operations")

    # 4a. Create blank presentation
    info("Creating blank presentation...")
    try:
        result = rpc("create_blank_presentation", session_id=SESSION_ID)
        ok(f"create_blank_presentation → {result}")
    except Exception as e:
        err(f"create_blank_presentation failed: {e}")
        return False

    # 4b. Get initial slide count
    try:
        result = rpc("get_slide_count", session_id=SESSION_ID)
        count = result["count"]
        ok(f"Initial slide count: {count}")
    except Exception as e:
        err(f"get_slide_count failed: {e}")
        return False

    # 4c. Add a slide
    info("Adding a slide (layout 1 = title+content)...")
    try:
        result = rpc("add_slide", session_id=SESSION_ID, layout_index=1)
        ok(f"add_slide → index={result['index']}, total={result['slide_count']}")
    except Exception as e:
        err(f"add_slide failed: {e}")
        return False

    # 4d. Set title
    info("Setting title on slide 0...")
    try:
        result = rpc("set_slide_title", session_id=SESSION_ID, slide_index=0, title="UNO Test Slide")
        ok(f"set_slide_title → {result}")
    except Exception as e:
        err(f"set_slide_title failed: {e}")
        return False

    # 4e. Set content
    info("Setting bullet content on slide 0...")
    try:
        result = rpc("set_slide_content", session_id=SESSION_ID, slide_index=0,
                     lines=["First bullet point", "Second bullet point", "Third bullet point"])
        ok(f"set_slide_content → {result}")
    except Exception as e:
        err(f"set_slide_content failed: {e}")
        return False

    # 4f. Apply font
    info("Applying amber font color to shape 0 on slide 0...")
    try:
        result = rpc("apply_font_to_shape", session_id=SESSION_ID,
                     slide_index=0, shape_id=0,
                     font_name="Liberation Sans", font_size_pt=36,
                     bold=True, color_hex="#F5A700")
        ok(f"apply_font_to_shape → {result}")
    except Exception as e:
        err(f"apply_font_to_shape failed: {e}")

    # 4g. Apply background color
    info("Applying white background to slide 0...")
    try:
        result = rpc("apply_background_color", session_id=SESSION_ID,
                     slide_index=0, color_hex="#FFFFFF")
        ok(f"apply_background_color → {result}")
    except Exception as e:
        err(f"apply_background_color failed: {e}")

    # 4h. Duplicate slide
    info("Duplicating slide 0...")
    try:
        result = rpc("duplicate_slide", session_id=SESSION_ID, index=0)
        ok(f"duplicate_slide → new_index={result['new_index']}, total={result['slide_count']}")
    except Exception as e:
        err(f"duplicate_slide failed: {e}")

    # 4i. Get thumbnail for slide 0
    info("Getting PNG thumbnail for slide 0 (1280x720)...")
    try:
        result = rpc("get_slide_thumbnail", session_id=SESSION_ID,
                     index=0, width_px=1280, height_px=720)
        png_b64 = result["png_b64"]
        png_bytes = base64.b64decode(png_b64)
        # Validate PNG magic bytes
        if png_bytes[:4] == b"\x89PNG":
            ok(f"Thumbnail is valid PNG: {len(png_bytes):,} bytes")
            # Save for visual inspection
            out_path = "/tmp/uno_test_slide0.png"
            with open(out_path, "wb") as f:
                f.write(png_bytes)
            ok(f"Saved thumbnail to {out_path} — open it to visually verify!")
        else:
            err(f"Thumbnail is not a valid PNG (got: {png_bytes[:8].hex()})")
    except Exception as e:
        err(f"get_slide_thumbnail failed: {e}")
        return False

    # 4j. List layouts
    info("Listing available master slide layouts...")
    try:
        result = rpc("list_available_layouts", session_id=SESSION_ID)
        layouts = result.get("layouts", [])
        ok(f"Available layouts ({len(layouts)}): {layouts[:5]}{'...' if len(layouts) > 5 else ''}")
    except Exception as e:
        err(f"list_available_layouts failed: {e}")

    # 4k. Export as pptx
    info("Exporting as pptx...")
    try:
        export_path = "/tmp/uno_test_export.pptx"
        result = rpc("export", session_id=SESSION_ID, output_path=export_path, fmt="pptx")
        if os.path.exists(export_path):
            ok(f"Exported pptx: {export_path} ({os.path.getsize(export_path):,} bytes)")
        else:
            err("Export succeeded but file not found on disk.")
    except Exception as e:
        err(f"export failed: {e}")

    # 4l. Export as PDF
    info("Exporting as pdf...")
    try:
        pdf_path = "/tmp/uno_test_export.pdf"
        result = rpc("export", session_id=SESSION_ID, output_path=pdf_path, fmt="pdf")
        if os.path.exists(pdf_path):
            ok(f"Exported PDF: {pdf_path} ({os.path.getsize(pdf_path):,} bytes)")
        else:
            err("PDF export succeeded but file not found.")
    except Exception as e:
        err(f"pdf export failed: {e}")

    # 4m. Delete a slide
    info("Deleting slide 1...")
    try:
        result = rpc("delete_slide", session_id=SESSION_ID, index=1)
        ok(f"delete_slide → remaining slides: {result['slide_count']}")
    except Exception as e:
        err(f"delete_slide failed: {e}")

    # 4n. Poll events
    info("Polling document-change events queue...")
    try:
        result = rpc("poll_events")
        events = result.get("events", [])
        ok(f"Polled {len(events)} pending document-change event(s)")
    except Exception as e:
        err(f"poll_events failed: {e}")

    # 4o. Close
    info("Closing document session...")
    try:
        result = rpc("close", session_id=SESSION_ID)
        ok(f"close → {result}")
    except Exception as e:
        err(f"close failed: {e}")

    return True


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", type=int, default=0,
                        help="Run only a specific stage (1-4). Default: run all.")
    args = parser.parse_args()

    run_all = args.stage == 0

    print("\n" + "═" * 60)
    print("  LibreOffice UNO Integration Test Suite")
    print("═" * 60)

    if run_all or args.stage == 1:
        soffice = stage1_daemon()
        if not soffice and not run_all:
            sys.exit(1)

    if run_all or args.stage == 2:
        ok_worker = stage2_worker()
        if not ok_worker and not run_all:
            sys.exit(1)
        if not ok_worker:
            print("\n⚠  Skipping stages 3 and 4 (worker failed to start).")
            sys.exit(1)

    if run_all or args.stage == 3:
        ok_client = stage3_client()
        if not ok_client and not run_all:
            sys.exit(1)

    if run_all or args.stage == 4:
        ok_doc = stage4_document()

    print(f"\n{'═'*60}")
    print("  Test run complete.")
    if run_all:
        print("  Open /tmp/uno_test_slide0.png to visually verify thumbnail output.")
        print("  Open /tmp/uno_test_export.pptx in LibreOffice to verify export.")
    print(f"{'═'*60}\n")

    # Cleanup worker
    if _worker_proc and _worker_proc.poll() is None:
        _worker_proc.terminate()


if __name__ == "__main__":
    main()
