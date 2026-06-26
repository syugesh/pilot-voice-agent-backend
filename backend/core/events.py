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
