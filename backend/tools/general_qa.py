"""
General Q&A tool — answers open-ended questions via Ollama (local), Gemini, or Groq,
grounded with live Tavily web search results when available so answers reflect
current information instead of only the model's training data.
Returns spoken_reply so bg_supervisor skips the extra generate_reply call.
Priority: Gemini (if key set) → Groq (if key set) → Ollama (always available locally)
"""
import asyncio, logging
from core.config import settings

logger = logging.getLogger("pilot.tools.general_qa")

GENERAL_QA_PROMPT = """You are PILOT, a helpful voice AI assistant.
Answer the user's question concisely in 1-3 natural spoken sentences.
No markdown, no lists, no special characters — plain conversational speech only.
If web search results are provided, treat them as ground truth — state the answer
directly and confidently. Never mention "search results", "conflicting information",
your training data, or that the user should check another source — just answer."""


async def general_qa(args: dict, session_id: str) -> dict:
    query = args.get("query", "").strip()
    if not query:
        return {"spoken_reply": "I didn't catch your question. Could you repeat that?"}

    web_context = await _web_search(query)

    reply = (
        await _try_ollama(query, web_context)
        or await _try_gemini(query, web_context)
        or await _try_groq(query, web_context)
        or "Sorry, I wasn't able to answer that right now. Please try again."
    )

    logger.info(f"general_qa answered: {reply[:80]}")
    return {"spoken_reply": reply, "query": query}


async def _web_search(query: str) -> str | None:
    """Fetch current web results from Tavily so the LLM can answer with up-to-date facts."""
    if not settings.TAVILY_API_KEY:
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": settings.TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "basic",
                    "include_answer": True,
                    "max_results": 4,
                },
            )
            if resp.status_code != 200:
                logger.warning(f"Tavily search {resp.status_code} for {query!r}")
                return None
            data = resp.json()
            parts = []
            if data.get("answer"):
                parts.append(data["answer"])
            for r in data.get("results", [])[:4]:
                snippet = (r.get("content") or "").strip()
                if snippet:
                    parts.append(f"{r.get('title', '')}: {snippet[:300]}")
            return "\n".join(parts) if parts else None
    except Exception as e:
        logger.warning(f"Tavily search failed for {query!r}: {e}")
        return None


def _build_messages(query: str, web_context: str | None) -> list[dict]:
    user_content = query
    if web_context:
        user_content = (
            f"Web search results:\n{web_context}\n\n"
            f"Using the results above where relevant, answer: {query}"
        )
    return [
        {"role": "system", "content": GENERAL_QA_PROMPT},
        {"role": "user",   "content": user_content},
    ]


async def _try_ollama(query: str, web_context: str | None = None) -> str | None:
    """Local Ollama fallback — always available when Ollama is running."""
    try:
        def _call() -> str:
            import ollama
            response = ollama.chat(
                model=settings.OLLAMA_MODEL,
                messages=_build_messages(query, web_context),
                think=False,              # disable Qwen3 thinking — fast spoken answers
                options={"num_predict": 360},  # 1-3 sentences is plenty
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


async def _try_gemini(query: str, web_context: str | None = None) -> str | None:
    if not settings.GEMINI_API_KEY:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel(
            "gemini-2.0-flash",
            system_instruction=GENERAL_QA_PROMPT,
        )
        messages = _build_messages(query, web_context)
        resp = await model.generate_content_async(messages[-1]["content"])
        return resp.text.strip()
    except Exception as e:
        logger.warning(f"Gemini general_qa failed: {e}")
        return None


async def _try_groq(query: str, web_context: str | None = None) -> str | None:
    if not settings.GROQ_API_KEY:
        return None
    try:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        resp = await client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=_build_messages(query, web_context),
            max_tokens=150,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Groq general_qa failed: {e}")
        return None
