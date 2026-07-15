"""
Prioritized Ollama gate.

The local Ollama serves one model slot: concurrent requests queue behind each
other inside Ollama, so a heavy background job (an 11-slide deck generation, a
streaming general_qa answer, a ReAct loop) will starve the latency-critical
Front-LLM classify that sits on every voice turn's hot path. That's exactly the
"Ollama classify error: timed out on every turn while a PPT generates" symptom.

This gate serializes all Ollama access through a single dispatcher and lets the
hot path jump the queue: a `priority="high"` request (classify) is always
dispatched before any waiting `priority="low"` (background) request, even ones
that were submitted earlier. Heavy work still runs — it just yields the next
slot to routing whenever routing is waiting, so the Front LLM stays responsive
under load.

Usage:
    from backend.core.llm_gate import ollama_gate
    result = await ollama_gate.run(lambda: some_blocking_ollama_call(), priority="high")

The callable is run in a worker thread (Ollama's client is sync), so callers
pass a zero-arg function, not a coroutine.
"""
import asyncio, logging, time, itertools

logger = logging.getLogger("pilot.llm_gate")


class OllamaGate:
    def __init__(self):
        # Two FIFO queues; the dispatcher always drains `high` before `low`.
        self._high: "asyncio.Queue[tuple]" = asyncio.Queue()
        self._low:  "asyncio.Queue[tuple]" = asyncio.Queue()
        self._wake = asyncio.Event()
        self._worker: asyncio.Task | None = None
        self._seq = itertools.count()

    def _ensure_worker(self):
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._dispatch())

    async def _dispatch(self):
        # Single consumer → guarantees at most one Ollama call in flight, and
        # always prefers the high queue. Sleeps on an Event when idle.
        while True:
            if self._high.empty() and self._low.empty():
                self._wake.clear()
                await self._wake.wait()
                continue
            q = self._high if not self._high.empty() else self._low
            fn, fut, label, prio, t0 = await q.get()
            if fut.cancelled():
                continue
            waited = time.time() - t0
            if waited > 1.0:
                logger.info(f"llm_gate: {label or prio} waited {waited:.1f}s for the Ollama slot")
            try:
                result = await asyncio.to_thread(fn)
                if not fut.cancelled():
                    fut.set_result(result)
            except Exception as e:
                if not fut.cancelled():
                    fut.set_exception(e)

    async def run(self, fn, *, priority: str = "low", label: str = "") -> object:
        self._ensure_worker()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        item = (fn, fut, label, priority, time.time())
        (self._high if priority == "high" else self._low).put_nowait(item)
        self._wake.set()
        return await fut


ollama_gate = OllamaGate()
