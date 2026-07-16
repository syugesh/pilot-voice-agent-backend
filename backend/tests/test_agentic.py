import pytest
from unittest.mock import AsyncMock, patch
from backend.services.agentic.orchestrator import OrchestratorAgent

@pytest.mark.asyncio
async def test_orchestrator_chit_chat_routing():
    # Instantiate the Orchestrator
    orchestrator = OrchestratorAgent()

    # Mock the LLM calls:
    # First call: category decision (JSON)
    # Second call: chit_chat direct reply (text)
    orchestrator.call_llm = AsyncMock()
    orchestrator.call_llm.side_effect = [
        '{"category": "chit_chat", "reason": "user said hello"}',
        "Hello! How can I help you today?"
    ]

    result = await orchestrator.route_and_execute(
        text="Hello PILOT",
        speaker_id="John",
        role="Manager",
        context=[]
    )

    assert result["action"] == "respond_now"
    assert result["preamble"] == "Hello! How can I help you today?"
    assert result["tool"] is None
    assert orchestrator.call_llm.call_count == 2


@pytest.mark.asyncio
async def test_orchestrator_travel_routing():
    orchestrator = OrchestratorAgent()

    # Mock LLM calls:
    # First call: category decision (JSON)
    # Second call: travel subagent tool execution selection (JSON)
    orchestrator.call_llm = AsyncMock()
    orchestrator.travel_agent.call_llm = AsyncMock()
    
    orchestrator.call_llm.return_value = '{"category": "travel", "reason": "user wants to fly to Mumbai"}'
    orchestrator.travel_agent.call_llm.return_value = (
        '{"action": "delegate", "preamble": "Searching for flights to Mumbai...", '
        '"tool": "flight_search", "args": {"destination": "BOM"}, "mode": "queue"}'
    )

    result = await orchestrator.route_and_execute(
        text="Find flights to Mumbai",
        speaker_id="John",
        role="Manager",
        context=[]
    )

    assert result["action"] == "delegate"
    assert result["tool"] == "flight_search"
    assert result["args"]["destination"] == "BOM"
    assert "Hello John!" in result["preamble"]


@pytest.mark.asyncio
async def test_orchestrator_general_qa_routing():
    orchestrator = OrchestratorAgent()

    orchestrator.call_llm = AsyncMock()
    orchestrator.call_llm.return_value = '{"category": "general_qa", "reason": "general knowledge question"}'

    result = await orchestrator.route_and_execute(
        text="Why is the sky blue?",
        speaker_id="John",
        role="Manager",
        context=[]
    )

    assert result["action"] == "delegate"
    assert result["tool"] == "general_qa"
    assert result["args"]["query"] == "Why is the sky blue?"


@pytest.mark.asyncio
@patch("backend.tools.ppt_copilot.ppt_navigate")
async def test_orchestrator_slide_navigation_intercept(mock_ppt_navigate):
    orchestrator = OrchestratorAgent()

    orchestrator.call_llm = AsyncMock()
    orchestrator.slides_agent.call_llm = AsyncMock()

    orchestrator.call_llm.return_value = '{"category": "slides", "reason": "user wants next slide"}'
    orchestrator.slides_agent.call_llm.return_value = (
        '{"action": "delegate", "preamble": "Moving forward!", '
        '"tool": "ppt_navigate", "args": {"direction": "next"}, "mode": "queue"}'
    )

    result = await orchestrator.route_and_execute(
        text="next slide",
        speaker_id="John",
        role="Manager",
        context=[],
        session_id="test_session"
    )

    # Verify tool was intercepted and executed immediately
    mock_ppt_navigate.assert_called_once_with({"direction": "next"}, "test_session")

    # Verify that the action was changed to respond_now to bypass supervisor queue
    assert result["action"] == "respond_now"
    assert result["tool"] is None
    assert "Hello John!" in result["preamble"]


# DISABLED: ppt_clear_presentation tool + its front_llm instant fast-path were
# both removed when ppt_copilot.py was replaced by the upstream GD-template
# implementation, which doesn't define this tool.
# @pytest.mark.asyncio
# @patch("backend.tools.ppt_copilot.ppt_clear_presentation")
# @patch("backend.tools.policy.policy_gate.check")
# @patch("backend.pipeline.front_llm._speak")
# async def test_front_llm_delegate_instant_clear(mock_speak, mock_policy_check, mock_clear_presentation):
#     mock_policy_check.return_value = True
#     mock_clear_presentation.return_value = {"status": "ok", "spoken_reply": "Presentation cleared."}
#
#     from backend.pipeline.front_llm import RouteDecision, _delegate
#
#     decision = RouteDecision(
#         action="delegate",
#         preamble="On it!",
#         tool="ppt_clear_presentation",
#         args={},
#         mode="queue",
#         speaker_id="John",
#         role="Manager",
#         session_id="test_session"
#     )
#
#     await _delegate(decision)
#
#     # Verify tool ran instantly in delegate
#     mock_clear_presentation.assert_called_once_with({}, "test_session")
#     # Verify vocal speech was triggered directly
#     mock_speak.assert_called_once_with("Presentation cleared.", "test_session")
