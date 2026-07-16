"""
General Q&A tool — answers open-ended questions via Ollama (local), Gemini, or Groq.
Returns spoken_reply so bg_supervisor skips the extra generate_reply call.
Priority: Gemini (if key set) → Groq (if key set) → Ollama (always available locally)
"""

import asyncio
import logging

from backend.core.config import settings

logger = logging.getLogger("pilot.tools.general_qa")

GENERAL_QA_PROMPT = """You are PILOT, a helpful voice AI assistant. Your reply is spoken out loud,
not read as a document — answer in 2-3 short, plain conversational sentences, maximum.
No headings, no bullet/numbered lists, no markdown formatting, no multi-section breakdowns.
Give the direct answer only — skip disclaimers, alternatives, and "here's how you could also..."
tangents unless the user explicitly asked for options.
Exception: if the user explicitly asks for code, reply with a short markdown code block plus
one sentence of context — nothing else."""


async def general_qa(args: dict, session_id: str) -> dict:
    query = args.get("query", "").strip()
    if not query:
        return {"spoken_reply": "I didn't catch your question. Could you repeat that?"}

    reply = (
        await _try_ollama(query)
        or await _try_groq(query)
        or await _try_gemini(query)
        or "Sorry, I wasn't able to answer that right now. Please try again."
    )

    logger.info(f"general_qa answered: {reply[:80]}")
    return {"spoken_reply": reply, "query": query}


async def _try_ollama(query: str) -> str | None:
    """Local Ollama fallback — always available when Ollama is running."""
    try:

        def _call() -> str:
            import ollama

            # A bounded-timeout client, not the bare ollama.chat() convenience
            # function — that uses a default client with NO request timeout,
            # so a slow/degraded Ollama hangs this call indefinitely. Since
            # this tool runs inside BGSupervisor's single global job queue
            # (backend/core/bg_supervisor.py), a hung call here doesn't just
            # fail this one question — it wedges every other queued voice
            # command behind it forever, with the UI stuck on "Processing".
            client = ollama.Client(host=settings.OLLAMA_BASE_URL, timeout=settings.OLLAMA_QA_TIMEOUT_S)
            response = client.chat(
                model=settings.OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": GENERAL_QA_PROMPT},
                    {"role": "user", "content": query},
                ],
                think=False,  # disable Qwen3 thinking — fast spoken answers
                options={
                    "num_predict": 220
                },  # 2-3 spoken sentences, with headroom for a short code block
                stream=False,
            )
            if isinstance(response, dict):
                return response["message"]["content"].strip()
            return response.message.content.strip()

        reply = await asyncio.to_thread(_call)
        logger.info(f"Ollama general_qa ok: {reply[:60]}")
        return reply
    except Exception as e:
        logger.warning(f"Ollama general_qa failed: {e}")
        return None


async def _try_gemini(query: str) -> str | None:
    if not settings.GEMINI_API_KEY:
        return None
    try:
        import google.generativeai as genai

        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel(
            "gemini-pro",
            system_instruction=GENERAL_QA_PROMPT,
        )
        resp = await model.generate_content_async(
            query, generation_config={"max_output_tokens": 220}
        )
        return resp.text.strip()
    except Exception as e:
        logger.warning(f"Gemini general_qa failed: {e}")
        return None


async def _try_groq(query: str) -> str | None:
    if not settings.GROQ_API_KEY:
        return None
    try:
        from groq import AsyncGroq

        client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        resp = await client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": GENERAL_QA_PROMPT},
                {"role": "user", "content": query},
            ],
            max_tokens=220,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Groq general_qa failed: {e}")
        return None
