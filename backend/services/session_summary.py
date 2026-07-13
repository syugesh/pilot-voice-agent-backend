"""
Session summary — turns a raw transcript + action log into a short, business-
readable summary (who spoke, what was discussed, what PILOT did, any open
items), using the same Ollama → Gemini → Groq fallback chain as general_qa.
"""
import asyncio, logging
from core.config import settings

logger = logging.getLogger("pilot.session_summary")

SUMMARY_PROMPT = """You are summarizing a voice AI session transcript for a business stakeholder.
Write a concise summary (3-6 sentences) covering:
- Who participated, using their actual names as given in the transcript
- What topics or requests were discussed
- What actions PILOT took (tool calls) and their outcomes
- Any unresolved items or clear next steps

Write in plain prose — no markdown, no bullet points, no preamble like
"Here is a summary". Be specific and factual; do not invent details that
are not present in the transcript or action list."""


def _format_transcript(transcripts: list[dict]) -> str:
    lines = []
    for t in transcripts:
        speaker = t.get("speaker") or "Unknown"
        text = (t.get("text") or "").strip()
        if text:
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def _format_actions(actions: list[dict]) -> str:
    if not actions:
        return "No tool actions were recorded."
    lines = []
    for a in actions:
        ok = a.get("decision") in ("ok", "allowed")
        lines.append(f"- {a.get('tool')}: {'succeeded' if ok else a.get('decision', 'unknown')}")
    return "\n".join(lines)


def _fallback_summary(transcripts: list[dict], actions: list[dict]) -> str:
    speakers = sorted({t.get("speaker") for t in transcripts if t.get("speaker")})
    return (
        f"{len(transcripts)} message(s) exchanged between {', '.join(speakers) or 'unknown speakers'}. "
        f"{len(actions)} tool action(s) were triggered. "
        "(AI summary unavailable right now — showing raw stats instead.)"
    )


ISSUE_SUMMARY_PROMPT = """You are a customer-support assistant writing a concise
issue synopsis for a service rep who is watching a live call. In 1-2 sentences,
state the customer's core problem, how long it has been happening, and what they
have already tried. Plain prose, no preamble, no markdown. Only use facts present
in the transcript — do not invent details."""


async def run_llm_chain(system_prompt: str, content: str, max_tokens: int = 300) -> str | None:
    """Shared Ollama → Gemini → Groq reasoning chain used by session summary,
    issue summary, and the resolution engine — one place to keep the fallback
    order and provider config consistent. Returns None if all providers fail
    (callers supply their own deterministic fallback)."""
    return (
        await _try_ollama(content, system_prompt, max_tokens)
        or await _try_gemini(content, system_prompt)
        or await _try_groq(content, system_prompt, max_tokens)
    )


async def summarize_issue(transcripts: list[dict]) -> str:
    """CSR-facing 'Customer reports X for Y; tried Z' synopsis for the live
    dashboard — reuses the shared LLM chain with an issue-focused prompt."""
    if not transcripts:
        return "No conversation yet."
    content = f"Transcript:\n{_format_transcript(transcripts)}\n\nWrite the issue synopsis."
    result = await run_llm_chain(ISSUE_SUMMARY_PROMPT, content, max_tokens=160)
    return result or "Live issue summary is unavailable right now."


async def summarize_session(transcripts: list[dict], actions: list[dict], usecase: str) -> str:
    if not transcripts:
        return "No conversation was recorded in this session."

    content = (
        f"Session type: {usecase}\n\n"
        f"Transcript:\n{_format_transcript(transcripts)}\n\n"
        f"Actions taken:\n{_format_actions(actions)}\n\n"
        "Summarize this session."
    )

    return (
        await _try_ollama(content)
        or await _try_gemini(content)
        or await _try_groq(content)
        or _fallback_summary(transcripts, actions)
    )


async def _try_ollama(content: str, system_prompt: str = SUMMARY_PROMPT, max_tokens: int = 300) -> str | None:
    try:
        import ollama

        def _call() -> str:
            response = ollama.chat(
                model=settings.OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": content},
                ],
                think=False,
                options={"num_predict": max_tokens},
                stream=False,
            )
            if isinstance(response, dict):
                return response["message"]["content"].strip()
            return response.message.content.strip()

        # Low priority — shared by summaries, the resolution engine, the care
        # observer and the ReAct agent. All background work; yields to routing.
        from core.llm_gate import ollama_gate
        return await ollama_gate.run(_call, priority="low", label="llm_chain")
    except Exception as e:
        logger.warning(f"Ollama summary failed: {e}")
        return None


async def _try_gemini(content: str, system_prompt: str = SUMMARY_PROMPT) -> str | None:
    if not settings.GEMINI_API_KEY:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.0-flash", system_instruction=system_prompt)
        resp = await model.generate_content_async(content)
        return resp.text.strip()
    except Exception as e:
        logger.warning(f"Gemini summary failed: {e}")
        return None


async def _try_groq(content: str, system_prompt: str = SUMMARY_PROMPT, max_tokens: int = 300) -> str | None:
    if not settings.GROQ_API_KEY:
        return None
    try:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        resp = await client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": content},
            ],
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Groq summary failed: {e}")
        return None
