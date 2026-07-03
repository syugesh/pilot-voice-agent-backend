import asyncio
import logging
from queues.bus import bus

logger = logging.getLogger("pilot.events")
_tasks: list[asyncio.Task] = []


async def startup_pipeline():
    from db.engine import migrate_db
    await migrate_db()

    from pipeline.vad.silero_vad import SileroVADWorker
    from pipeline.vad.smart_turn import SmartTurnWorker
    from pipeline.diarizer import DiarizerWorker
    from pipeline.identity_resolver import IdentityResolverWorker
    from pipeline.asr_worker import ASRWorker
    from pipeline.front_llm import FrontLLMWorker
    from core.bg_supervisor import bg_supervisor

    # Warm up the WeSpeaker embedding model in the background now, rather than
    # lazily on first use. Without this, the VAD's streaming per-window embed
    # extraction (services/enrollment.py extract()) runs before the model has
    # loaded and silently falls back to a random hash-based stub embedding for
    # every turn until something else happens to trigger the lazy load — which
    # made early speaker identification look like it was randomly failing.
    from services.enrollment import embed_provider
    asyncio.create_task(asyncio.to_thread(embed_provider.load), name="WeSpeakerWarmup")

    workers = [
        SileroVADWorker(bus),
        SmartTurnWorker(bus),
        DiarizerWorker(bus),
        IdentityResolverWorker(bus),
        ASRWorker(bus),
        FrontLLMWorker(bus),
    ]
    for w in workers:
        t = asyncio.create_task(w.run(), name=type(w).__name__)
        _tasks.append(t)
        logger.info(f"Worker started: {type(w).__name__}")

    t = asyncio.create_task(bg_supervisor.run(), name="BGSupervisor")
    _tasks.append(t)


async def shutdown_pipeline():
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    logger.info("All workers stopped")
