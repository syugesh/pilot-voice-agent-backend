import asyncio, logging
from queues.bus import QueueBus, TurnSegment

logger = logging.getLogger("pilot.smart_turn")

class SmartTurnWorker:
    def __init__(self, bus: QueueBus):
        self.bus = bus

    async def run(self):
        logger.info("SmartTurn worker started")
        while True:
            seg: TurnSegment = await self.bus.turn_q.get()
            # Stub: always complete. Wire pipecat smart-turn here for real semantic check.
            await self.bus.diar_q.put(seg)   # ← correct: forward to diar_q, NOT back to turn_q
