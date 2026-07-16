"""
Background agent — Gemini (primary) → Groq (fallback) → Ollama Mistral/Qwen (fallback) → tool-only (stub).
Uses API keys from .env: GEMINI_API_KEY, GROQ_API_KEY, OLLAMA_BASE_URL, OLLAMA_MODEL
"""

import json
import logging

import httpx

from backend.core.config import settings

logger = logging.getLogger("pilot.bg_agent")

AGENT_SYSTEM = """You are PILOT's background agent. A tool has been called and returned a result.
Generate a concise, natural spoken response (2-3 sentences max) summarising the result for the user.
Be conversational, helpful, and specific. No markdown, no lists — just natural speech."""


async def _gemini_reply(tool: str, result: dict, context: list) -> str | None:
    if not settings.GEMINI_API_KEY:
        return None
    try:
        import google.generativeai as genai

        genai.configure(api_key=settings.GEMINI_API_KEY)
        # Use gemini-2.5-flash as it is the absolute latest and highest-capability Gemini model for MCP tool calling & structured analysis
        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = (
            f"Tool called: {tool}\n"
            f"Result: {json.dumps(result, indent=2)[:1500]}\n\n"
            f"Generate a highly detailed, comprehensive spoken response of 3 to 4 complete sentences summarizing this data explicitly for the user. Do not cut off, synthesize the entire answer conversationally:"
        )
        resp = await model.generate_content_async(prompt)
        return resp.text.strip()
    except Exception as e:
        logger.warning(f"Gemini reply failed: {e}")
        return None


async def _groq_reply(tool: str, result: dict) -> str | None:
    if not settings.GROQ_API_KEY:
        return None
    try:
        from groq import AsyncGroq

        client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        prompt = (
            f"Tool called: {tool}\n"
            f"Result: {json.dumps(result, indent=2)[:1500]}\n\n"
            f"Generate a highly detailed, comprehensive spoken response of 3 to 4 complete sentences summarizing this data explicitly for the user. Do not cut off, synthesize the entire answer conversationally:"
        )
        resp = await client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "system", "content": AGENT_SYSTEM}, {"role": "user", "content": prompt}],
            max_tokens=250,
            temperature=0.3,
        )
        if resp.choices[0].message.content:
            return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Groq reply failed: {e}")
        return None


async def _ollama_reply(tool: str, result: dict) -> str | None:
    """Ollama local models fallback when no Gemini keys are provided."""
    try:
        url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/generate"
        prompt = (
            f"{AGENT_SYSTEM}\n\n"
            f"Tool called: {tool}\n"
            f"Result Data: {json.dumps(result)[:1000]}\n\n"
            f"Provide a comprehensive, highly-detailed natural response of 3-4 full sentences summarizing this data explicitly for the user:"
        )

        # Setup prioritized fallback models: default -> qwen2.5:7b -> fallback
        preferred_models = [
            settings.OLLAMA_MODEL,
            "qwen2.5:7b",
            "qwen3.5:2b",
            "qwen3.5:3b",
            "llama3.2:latest",
        ]
        models_to_try = []
        for m in preferred_models:
            if m and m not in models_to_try:
                models_to_try.append(m)

        for active_model in models_to_try:
            try:
                # Increased timeout to 120s to allow heavy local 8B models to complete background processing safely
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(
                        url, json={"model": active_model, "prompt": prompt, "stream": False}
                    )
                    if resp.status_code == 200:
                        return resp.json().get("response", "").strip()
            except Exception as model_err:
                logger.warning(f"Ollama local model '{active_model}' failed for reply: {model_err}")
                continue
    except Exception as e:
        logger.warning(f"Ollama local reply failed: {e}")
    return None


async def generate_reply(tool: str, result: dict, context: list = []) -> str | None:
    """Orchestrates background reply using the agentic sub-agent system."""
    # Fast-intercept: If the tool is a simple slide navigation command, do not call heavy LLMs to explain the action.
    if tool in ("ppt_navigate", "ppt_jump_to_title", "ppt_delete_slide"):
        return None

    # print(f"\n🧠 [AGENTIC ORCHESTRATOR] Processing background agent reply for tool '{tool}'.")

    try:
        from backend.services.agentic.subagents import TravelSubAgent, SlidesSubAgent
        from backend.services.agentic.orchestrator import OrchestratorAgent

        # Select the correct subagent based on the tool executed
        if tool in ("flight_search", "flight_book", "kb_search"):
            agent = TravelSubAgent()
        elif tool.startswith("ppt_"):
            agent = SlidesSubAgent()
        else:
            agent = OrchestratorAgent()

        prompt = (
            f"Tool called: {tool}\n"
            f"Result: {json.dumps(result, indent=2)[:1500]}\n\n"
            f"Generate a concise, natural spoken response (2-3 sentences max) summarizing this result for the user. "
            f"Ensure your response matches your persona instructions. Do not write markdown or lists — keep it purely conversational."
        )

        reply = await agent.call_llm(prompt)
        if reply:
            # print(f'✅ [LLM SUCCESS] {agent.name} responded successfully! Reply: "{reply[:80]}..."')
            return reply

    except Exception as e:
        logger.error(f"Agentic background reply failed: {e}. Falling back to default LLM calls...")

    # Standard fallback LLM calls
    reply = await _groq_reply(tool, result)
    if reply:
        return reply

    reply = await _gemini_reply(tool, result, context)
    if reply:
        return reply

    reply = await _ollama_reply(tool, result)
    if reply:
        return reply

    print(
        "❌ [LLM FAILURE] All active LLM providers failed to respond. Falling back to local hardcoded templates."
    )
    return None
