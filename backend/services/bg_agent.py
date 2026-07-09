"""
Background agent — Gemini (primary) → Groq (fallback) → Ollama Mistral/Qwen (fallback) → tool-only (stub).
Uses API keys from .env: GEMINI_API_KEY, GROQ_API_KEY, OLLAMA_BASE_URL, OLLAMA_MODEL
"""
import logging, json, httpx
from core.config import settings

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
        prompt = (f"Tool called: {tool}\n"
                  f"Result: {json.dumps(result, indent=2)[:1500]}\n\n"
                  f"Generate a highly detailed, comprehensive spoken response of 3 to 4 complete sentences summarizing this data explicitly for the user. Do not cut off, synthesize the entire answer conversationally:")
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
        prompt = (f"Tool called: {tool}\n"
                  f"Result: {json.dumps(result, indent=2)[:1500]}\n\n"
                  f"Generate a highly detailed, comprehensive spoken response of 3 to 4 complete sentences summarizing this data explicitly for the user. Do not cut off, synthesize the entire answer conversationally:")
        resp = await client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": AGENT_SYSTEM},
                {"role": "user", "content": prompt}
            ],
            max_tokens=250,
            temperature=0.3
        )
        if resp.choices[0].message.content:
            return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Groq reply failed: {e}")
        return None


async def _ollama_reply(tool: str, result: dict) -> str | None:
    """Ollama local model fallback when no Gemini/Groq keys are configured (or those calls failed)."""
    try:
        url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/generate"
        prompt = (f"{AGENT_SYSTEM}\n\n"
                  f"Tool called: {tool}\n"
                  f"Result Data: {json.dumps(result)[:1000]}\n\n"
                  f"Provide a comprehensive, highly-detailed natural response of 3-4 full sentences summarizing this data explicitly for the user:")

        # Just the configured model, with one small/fast fallback if it isn't
        # pulled. This used to sequentially try up to 5 models at 120s each —
        # a single not-pulled or slow model could stall a voice reply for
        # minutes. 20s is generous for a short (~150 token) local generation;
        # if a pulled model hasn't responded by then, waiting longer isn't
        # productive on the latency-sensitive voice path.
        models_to_try = [settings.OLLAMA_MODEL]
        if settings.OLLAMA_MODEL != "qwen3.5:2b":
            models_to_try.append("qwen3.5:2b")

        for active_model in models_to_try:
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(url, json={
                        "model": active_model,
                        "prompt": prompt,
                        "stream": False
                    })
                    if resp.status_code == 200:
                        return resp.json().get("response", "").strip()
            except Exception as model_err:
                logger.warning(f"Ollama local model '{active_model}' failed for reply: {model_err}")
                continue
    except Exception as e:
        logger.warning(f"Ollama local reply failed: {e}")
    return None


async def generate_reply(tool: str, result: dict, context: list = []) -> str | None:
    """Orchestrates background reply: tries Groq first, then Gemini, then falls back to local Ollama."""
    # Fast-intercept: If the tool is a simple slide navigation command, do not call heavy LLMs to explain the action.
    # Just return None so the background supervisor can use the fast local fallback templates instantly.
    if tool in ("ppt_navigate", "ppt_jump_to_title", "ppt_delete_slide"):
        return None

    logger.info(f"Generating background reply for tool '{tool}'")

    reply = await _groq_reply(tool, result)
    if reply:
        logger.info(f"Groq reply for {tool}: {reply[:60]}")
        return reply

    reply = await _gemini_reply(tool, result, context)
    if reply:
        logger.info(f"Gemini reply for {tool}: {reply[:60]}")
        return reply

    reply = await _ollama_reply(tool, result)
    if reply:
        logger.info(f"Ollama reply for {tool}: {reply[:60]}")
        return reply

    logger.warning(f"All background reply providers failed/unconfigured for tool '{tool}' — falling back to hardcoded templates")
    return None
