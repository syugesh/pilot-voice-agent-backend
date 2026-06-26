import pytest, asyncio
from core.bg_supervisor import BGSupervisor, Job

@pytest.mark.asyncio
async def test_interrupt_mode():
    sup = BGSupervisor()
    # Just verify submit doesn't raise
    job = Job(job_id="j1", tool="ppt_navigate", args={"direction":"next"},
              mode="queue", speaker_id="Alice", role="developer", session_id="s1")
    await sup.submit(job)
    assert sup._queue.qsize() == 1
