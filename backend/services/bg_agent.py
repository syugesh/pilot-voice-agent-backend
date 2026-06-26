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
    """Ollama local models fallback when no Gemini keys are provided."""
    try:
        url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/generate"
        prompt = (f"{AGENT_SYSTEM}\n\n"
                  f"Tool called: {tool}\n"
                  f"Result Data: {json.dumps(result)[:1000]}\n\n"
                  f"Provide a comprehensive, highly-detailed natural response of 3-4 full sentences summarizing this data explicitly for the user:")
        
        # Setup prioritized fallback models: default -> qwen3:8b -> qwen3.5:2b/3b -> fallback
        preferred_models = [settings.OLLAMA_MODEL, "qwen3.5:2b" ,"qwen3.5:3b","qwen3:8b", "llama3.2:latest"]
        models_to_try = []
        for m in preferred_models:
            if m and m not in models_to_try:
                models_to_try.append(m)
                
        for active_model in models_to_try:
            try:
                # Increased timeout to 120s to allow heavy local 8B models to complete background processing safely
                async with httpx.AsyncClient(timeout=120.0) as client:
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
    """Orchestrates background reply: tries Gemini first, then Groq API, then falls back to local Ollama."""
    # Fast-intercept: If the tool is a simple slide navigation command, do not call heavy LLMs to explain the action.
    # Just return None so the background supervisor can use the fast local fallback templates instantly.
    if tool in ("ppt_navigate", "ppt_jump_to_title", "ppt_delete_slide"):
        return None

    print(f"\n🧠 [LLM ORCHESTRATOR] Processing background agent reply for tool '{tool}'.")
    

    # 1. Second Priority: Fall back to Groq API
    print(f"📡 [LLM TRY 1/3] Gemini failed/unconfigured. Falling back to Groq API (Llama-3.1)...")
    reply = await _groq_reply(tool, result)
    if reply:
        print(f"✅ [LLM SUCCESS] Groq responded successfully! Reply: \"{reply[:80]}...\"")
        logger.info(f"Groq reply for {tool}: {reply[:60]}")
        return reply
    

    # 2. First Priority: Try Gemini API
    print(f"📡 [LLM TRY 2/3] Directing request to Gemini API...")
    reply = await _gemini_reply(tool, result, context)
    if reply:
        print(f"✅ [LLM SUCCESS] Gemini responded successfully! Reply: \"{reply[:80]}...\"")
        logger.info(f"Gemini reply for {tool}: {reply[:60]}")
        return reply

    
    

    # 3. Third Priority: Fall back to local Ollama model
    print(f"📡 [LLM TRY 3/3] Groq failed/unconfigured. Falling back to local Ollama runner ({settings.OLLAMA_MODEL})...")
    reply = await _ollama_reply(tool, result)
    if reply:
        print(f"✅ [LLM SUCCESS] Local Ollama responded successfully! Reply: \"{reply[:80]}...\"")
        logger.info(f"Ollama reply for {tool}: {reply[:60]}")
        return reply
        
    print(f"❌ [LLM FAILURE] All active LLM providers (Gemini, Groq, Ollama) failed to respond or are unconfigured. Falling back to local hardcoded templates.")
    return None



# """
# Background agent — Gemini (primary) → Groq (fallback) → tool-only (stub).
# Uses API keys from .env: GEMINI_API_KEY, GROQ_API_KEY
# """
# import logging, json
# from core.config import settings

# logger = logging.getLogger("pilot.bg_agent")

# AGENT_SYSTEM = """You are PILOT's background agent. A tool has been called and returned a result.
# Generate a concise, natural spoken response (2-3 sentences max) summarising the result for the user.
# Be conversational, helpful, and specific. No markdown, no lists — just natural speech."""


# async def _gemini_reply(tool: str, result: dict, context: list) -> str | None:
#     if not settings.GEMINI_API_KEY:
#         return None
#     try:
#         import google.generativeai as genai
#         genai.configure(api_key=settings.GEMINI_API_KEY)
#         model = genai.GenerativeModel("gemini-2.0-flash")
#         prompt = (f"Tool called: {tool}\n"
#                   f"Result: {json.dumps(result, indent=2)[:800]}\n\n"
#                   f"Generate a natural spoken summary for the user:")
#         resp = await model.generate_content_async(prompt)
#         return resp.text.strip()
#     except Exception as e:
#         logger.warning(f"Gemini reply failed: {e}")
#         return None


# async def _groq_reply(tool: str, result: dict) -> str | None:
#     if not settings.GROQ_API_KEY:
#         return None
#     try:
#         from groq import AsyncGroq
#         client = AsyncGroq(api_key=settings.GROQ_API_KEY)
#         prompt = (f"Tool: {tool}\nResult: {json.dumps(result)[:600]}\n"
#                   f"Summarise in 2 sentences naturally for a voice assistant user:")
#         resp = await client.chat.completions.create(
#             model="llama-3.1-8b-instant",
#             messages=[
#                 {"role": "system", "content": AGENT_SYSTEM},
#                 {"role": "user",   "content": prompt}
#             ],
#             max_tokens=120,
#         )
#         return resp.choices[0].message.content.strip()
#     except Exception as e:
#         logger.warning(f"Groq reply failed: {e}")
#         return None


# async def generate_reply(tool: str, result: dict, context: list = []) -> str | None:
#     """Try Gemini → Groq → None (fallback to hardcoded in bg_supervisor)."""
#     reply = await _gemini_reply(tool, result, context)
#     if reply:
#         logger.info(f"Gemini reply for {tool}: {reply[:60]}")
#         return reply
#     reply = await _groq_reply(tool, result)
#     if reply:
#         logger.info(f"Groq reply for {tool}: {reply[:60]}")
#         return reply
#     return None
