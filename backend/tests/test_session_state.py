import pytest
from core.session_state import get_state, clear_state

def test_ring_buffer():
    state = get_state("test-session")
    for i in range(60):
        state.add_span({"text": f"span {i}", "speaker": "Alice"})
    ctx = state.get_context(10)
    assert len(ctx) == 10
    assert ctx[-1]["text"] == "span 59"
    clear_state("test-session")

def test_get_context_empty():
    state = get_state("empty-session")
    assert state.get_context() == []
    clear_state("empty-session")
