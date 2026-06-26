import pytest, asyncio, time
from queues.bus import QueueBus, RawAudioChunk

@pytest.mark.asyncio
async def test_queue_backpressure():
    bus = QueueBus()
    chunks_added = 0
    for i in range(bus.raw_audio_q.maxsize + 5):
        try:
            bus.raw_audio_q.put_nowait(
                RawAudioChunk(pcm=b"\x00" * 320, session_id="t", timestamp=time.time())
            )
            chunks_added += 1
        except Exception:
            break
    assert chunks_added == bus.raw_audio_q.maxsize

@pytest.mark.asyncio
async def test_emit_event():
    bus = QueueBus()
    await bus.emit_event("transcript", {"text": "hello world"}, "s1")
    evt = bus.event_q.get_nowait()
    assert evt.type == "transcript"
    assert evt.payload["text"] == "hello world"
    assert evt.session_id == "s1"
