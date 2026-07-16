import asyncio
import logging

from backend.queues.bus import bus

logger = logging.getLogger("pilot.events")
_tasks: list[asyncio.Task] = []


async def startup_pipeline():
    from backend.db.engine import init_db
    await init_db()

    from backend.core.bg_supervisor import bg_supervisor
    from backend.pipeline.asr_worker import ASRWorker
    from backend.pipeline.diarizer import DiarizerWorker
    from backend.pipeline.front_llm import FrontLLMWorker
    from backend.pipeline.identity_resolver import IdentityResolverWorker
    from backend.pipeline.vad.silero_vad import SileroVADWorker
    from backend.pipeline.vad.smart_turn import SmartTurnWorker

    # NOTE: No persistent LibreOffice daemon is started here.
    # On macOS, LibreOffice's AquaSalInstance unconditionally connects to the
    # WindowServer even in --headless mode, causing the dock icon to bounce.
    # All PPTX-to-PDF conversions use _run_libreoffice_headless() in ppt.py
    # which spawns short-lived isolated processes per conversion instead.

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
    # This is a Job Scheduler. It doesn't process audio; instead, it waits for the FrontLLMWorker to say: "Here is a background job (e.g., flight booking, slide creation, database write). Run this."
    # If the background agent was placed inside the main pipeline list, a slow task would block the queues, freezing speech recognition and audio playback.
    t = asyncio.create_task(bg_supervisor.run(), name="BGSupervisor")
    _tasks.append(t)

    # Warm the customer-sentiment model in the background so the first live
    # call doesn't pay the (slow) model-load cost — feeds the Customer
    # Resolution dashboard's frustration meter.
    from backend.services.sentiment import sentiment_provider
    asyncio.create_task(asyncio.to_thread(sentiment_provider.load), name="SentimentWarmup")


async def shutdown_pipeline():
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    logger.info("All workers stopped")
