"""
PILOT — Portable Intelligent Listener for Open Tasking
FastAPI app factory + lifespan (startup / shutdown)
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import asyncio, logging, os

from core.config import settings
from db.engine import init_db
from core.events import startup_pipeline, shutdown_pipeline
from api.auth import router as auth_router
from api.sessions import router as sessions_router
from api.enrollment import router as enrollment_router
from api.transcripts import router as transcript_router
from api.ppt import router as ppt_router
from api.flights import router as flights_router
from api.ws_audio import router as ws_audio_router
from api.ws_events import router as ws_events_router

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

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# ── REST routers ──
app.include_router(auth_router,       prefix="/api/v1/auth",        tags=["auth"])
app.include_router(sessions_router,   prefix="/api/v1/sessions",    tags=["sessions"])
app.include_router(enrollment_router, prefix="/api/v1/enrollment",  tags=["enrollment"])
app.include_router(transcript_router, prefix="/api/v1/transcripts", tags=["transcripts"])
app.include_router(ppt_router,        prefix="/api/v1/ppt",         tags=["ppt"])
app.include_router(flights_router,    prefix="/api/v1/flights",     tags=["flights"])

# ── WebSocket routers ──
app.include_router(ws_audio_router)
app.include_router(ws_events_router)

# ── Frontend (React build / static) ──
frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="static")
