"""
PILOT — Portable Intelligent Listener for Open Tasking
FastAPI app factory + lifespan (startup / shutdown)
"""
import logging
import os
import warnings
from contextlib import asynccontextmanager

try:
    from jwt.exceptions import InsecureKeyLengthWarning
    warnings.filterwarnings("ignore", category=InsecureKeyLengthWarning)
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.auth import router as auth_router
from backend.api.enrollment import router as enrollment_router
from backend.api.flights import router as flights_router
from backend.api.groups import router as groups_router
from backend.api.ppt import router as ppt_router
from backend.api.resolution import router as resolution_router
from backend.api.sessions import router as sessions_router
from backend.api.telephony import router as telephony_router
from backend.api.transcripts import router as transcript_router
from backend.api.ws_audio import router as ws_audio_router
from backend.api.ws_events import router as ws_events_router
from backend.api.ws_telephony import router as ws_telephony_router
from backend.core.events import shutdown_pipeline, startup_pipeline
from backend.core.security import decode_token
from backend.db.engine import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("pilot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("═══ PILOT starting up ═══")
    await init_db()
    await startup_pipeline()
    logger.info("═══ PILOT ready ✓ ═══")
    yield
    logger.info("═══ PILOT shutting down ═══")
    await shutdown_pipeline()


app = FastAPI(title="PILOT Voice AI Copilot", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    # Also allow any private-LAN IP (192.168.x.x, 10.x.x.x, 172.16-31.x.x) on
    # the usual dev ports, so a Talkinia frontend on another machine on the
    # same network can reach this backend for cross-machine meetings — see
    # NEXT_PUBLIC_PILOT_BACKEND_HOST in TALKINIA_STREAM/components/MeetingRoom.tsx.
    allow_origin_regex=r"http://(192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}):(3000|5173)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Authentication Middleware Proxy for REST API routes ──



@app.middleware("http")
async def route_auth_middleware(request: Request, call_next):
    # Exempt all preflight OPTIONS requests from authentication checks
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    if path.startswith("/api/v1/"):
        # Exempt public authentication paths AND slide viewer pages from auth headers (iframes cannot send bearer tokens)
        is_public = any(
            p in path
            for p in [
                "/auth/signup",
                "/auth/login",
                # Google SSO redirect flow — hit directly by the browser
                # (navigating away to Google) and by Google's own redirect
                # back with ?code=&state=, neither of which can carry a
                # PILOT Bearer token.
                "/auth/sso/google",
                "/auth/send-otp",
                "/auth/verify-otp",
                "/auth/forgot-password",
                "/auth/verify-forgot-otp",
                # Anonymous pre-account voice capture (normal signup wizard,
                # before OTP verification — no token exists yet). Already-
                # authenticated users hitting the same endpoints (e.g. Google
                # SSO completing enrollment separately) still send a real
                # Bearer token; enrollment/audio uses it if present, see
                # api/enrollment.py.
                "/enrollment/start",
                "/enrollment/audio",
                # Twilio calls this webhook directly (no PILOT Bearer token
                # to send) — verified instead via the Twilio request
                # signature inside the handler itself, see api/telephony.py.
                "/telephony/voice",
                "/ppt/viewer",
                "/ppt/upload_stream",
                "/ppt/upload_instant",
                "/ppt/render_stream",
                "/ppt/download",
                "/ppt/callback",
                "/ppt/image",
                "/compile-notes",
            ]
        )
        if not is_public:
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return JSONResponse(status_code=401, content={"detail": "Missing or invalid credentials"})
            token = auth_header.split(" ")[1]
            try:
                decode_token(token)
            except Exception:
                return JSONResponse(status_code=401, content={"detail": "Missing or invalid credentials"})

    response = await call_next(request)
    return response


# ── REST routers ──
app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(sessions_router, prefix="/api/v1/sessions", tags=["sessions"])
app.include_router(enrollment_router, prefix="/api/v1/enrollment", tags=["enrollment"])
app.include_router(transcript_router, prefix="/api/v1/transcripts", tags=["transcripts"])
app.include_router(ppt_router, prefix="/api/v1/ppt", tags=["ppt"])
app.include_router(flights_router, prefix="/api/v1/flights", tags=["flights"])
app.include_router(groups_router, prefix="/api/v1/groups", tags=["groups"])
app.include_router(resolution_router, prefix="/api/v1/resolution", tags=["resolution"])
app.include_router(telephony_router, prefix="/api/v1/telephony", tags=["telephony"])

# ── WebSocket routers ──
app.include_router(ws_audio_router)
app.include_router(ws_events_router)
app.include_router(ws_telephony_router)

# ── Frontend (React build / static) ──
frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")
    app.mount("/slides", StaticFiles(directory=os.path.join(frontend_dist, "slides")), name="slides")

    from fastapi.responses import FileResponse

    @app.get("/{catchall:path}", include_in_schema=False)
    async def serve_spa(catchall: str):
        # If requests start with api/ or ws/ they should not be caught here
        if catchall.startswith("api/") or catchall.startswith("ws/"):
            raise HTTPException(status_code=404)
        return FileResponse(os.path.join(frontend_dist, "index.html"))
