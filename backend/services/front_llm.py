"""
Front LLM — Ollama primary with per-session conversation memory, keyword fallback.
Uses sync ollama.chat() in asyncio.to_thread() (same pattern as core/llm.py reference).
Key fixes vs previous version:
  - Removed format="json" which broke Qwen3 thinking mode
  - Sync client is more reliable than AsyncClient for local Ollama
  - Per-session ConversationMemory for natural multi-turn dialogue
  - num_predict cap prevents slow responses
"""
import asyncio, json, logging, re, time, datetime
from core.config import settings

_MONTH_MAP = {
    "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
    "jan":1,"feb":2,"mar":3,"apr":4,"jun":6,"jul":7,"aug":8,
    "sep":9,"sept":9,"oct":10,"nov":11,"dec":12,
}
_MONTH_PAT = "(?:" + "|".join(_MONTH_MAP.keys()) + ")"

def _parse_natural_date(text: str) -> str | None:
    """Convert 'July 17' / '17th July' / 'July 17th' → ISO date string."""
    t = text.lower()
    today = datetime.date.today()

    def _make(month: int, day: int) -> str:
        year = today.year
        try:
            d = datetime.date(year, month, day)
            if d < today:
                d = datetime.date(year + 1, month, day)
            return d.isoformat()
        except ValueError:
            return today.isoformat()

    # "July 17" / "July 17th"
    m = re.search(rf'\b({_MONTH_PAT})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b', t)
    if m:
        return _make(_MONTH_MAP[m.group(1)], int(m.group(2)))

    # "17th July" / "17 July"
    m = re.search(rf'\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_PAT})\b', t)
    if m:
        return _make(_MONTH_MAP[m.group(2)], int(m.group(1)))

    # Bare ordinal day, no month given — "for the 10th", "on 10th" → nearest
    # upcoming occurrence (this month, or next month if that day already passed).
    # Ordinal suffix is required so this doesn't fire on "1st class" etc.
    m = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)\b(?!\s*class)', t)
    if m:
        day = int(m.group(1))
        if 1 <= day <= 31:
            month, year = today.month, today.year
            try:
                d = datetime.date(year, month, day)
                if d < today:
                    month = month + 1 if month < 12 else 1
                    year  = year if month != 1 else year + 1
                    d = datetime.date(year, month, day)
                return d.isoformat()
            except ValueError:
                pass

    return None

logger = logging.getLogger("pilot.front_llm")


def _extract_json(content: str) -> str:
    """Strip <think>...</think> blocks (Qwen3 thinking model) then extract JSON."""
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
    m = re.search(r'\{.*\}', content, re.DOTALL)
    return m.group(0) if m else content


SYSTEM_PROMPT = """You are PILOT — a real-time voice AI copilot routing engine.

Output ONLY valid JSON — no markdown, no prose, no explanation:
{
  "action": "ignore" | "respond_now" | "delegate",
  "preamble": "<spoken reply ≤10 words, warm and human>",
  "tool": "<tool_name or null>",
  "args": {},
  "mode": "queue" | "interrupt"
}

ACTION RULES (strict):
- "delegate": use when calling ANY tool. Set tool + args. NEVER use respond_now when a tool is needed.
- "respond_now": ONLY for greetings and simple social acknowledgments ("hello", "thanks", "good job"). tool MUST be null. preamble MUST be ≤10 words. DO NOT use respond_now to answer factual questions.
- "ignore": filler words, noise, silence, negated commands, confidence < 0.6.

CRITICAL — DO NOT fake answers in preamble:
  BAD:  {"action":"respond_now","preamble":"Linear classification separates data with a line.","tool":null}
  GOOD: {"action":"delegate","preamble":"Let me explain that!","tool":"general_qa","args":{"query":"..."}}
The preamble is just a brief acknowledgment. The tool produces the real answer.

PPT TOOLS: ppt_navigate(direction:next|prev|first|last), ppt_jump_to_title(query,slide_number), ppt_summarize(), ppt_delete_slide(slide_number?)
"delete slide 10" → ppt_delete_slide(slide_number=9)   "delete this slide" → ppt_delete_slide()
NEVER route delete/remove commands to ppt_jump_to_title or ppt_navigate.
CARE TOOLS: ticket_create, ticket_update, ticket_close, kb_search(query), crm_lookup, flight_search, flight_book
GENERAL: general_qa(query) — world knowledge, facts, concepts, definitions, science, "who is", "what is", "tell me", "explain", "how does", "why", "what does", "describe", "difference between"

slide_number is 0-indexed: "go to slide 7" → slide_number=6
Use ppt_summarize when user asks to summarize, overview, or describe the presentation.
Use general_qa for ANY factual or conceptual question — including questions about things shown on slides.
Use kb_search ONLY for company-internal policies, procedures, or internal documents — NOT general world knowledge.
"""

_QUESTION_STARTERS = {
    "what", "who", "where", "when", "why", "how", "which", "whose", "whom",
    "explain", "tell", "describe", "define", "difference", "what's", "who's",
    "what are", "what is", "how does", "how do", "why does", "why is",
    "can you explain", "could you explain", "what does", "how can",
}


def _fix_respond_now_questions(result: dict, text: str, usecase: str) -> dict:
    """
    If the model chose respond_now for a knowledge question (where it should have
    delegated to general_qa), override it. Also fires when preamble is unusually long
    (model faked an answer in the preamble field).
    """
    if result.get("action") != "respond_now":
        return result

    t = text.lower().strip().rstrip("?.,!")
    words = t.split()

    # Detect question / explanation request
    is_question = (
        text.strip().endswith("?")
        or (words and words[0] in _QUESTION_STARTERS)
        or any(t.startswith(s) for s in _QUESTION_STARTERS)
    )

    # Detect fake answer in preamble (long preamble = model put the answer there)
    preamble = result.get("preamble") or ""
    preamble_too_long = len(preamble.split()) > 10

    if is_question or preamble_too_long:
        logger.debug(
            f"respond_now override → general_qa "
            f"(is_question={is_question}, preamble_words={len(preamble.split())})"
        )
        return {
            "action": "delegate",
            "preamble": "Let me look that up!",
            "tool": "general_qa",
            "args": {"query": text},
            "mode": "queue",
        }

    return result


_KEYWORDS = [
    (["next slide","go forward","advance","next one"],
     {"action":"delegate","preamble":"Moving forward!","tool":"ppt_navigate","args":{"direction":"next"},"mode":"queue"}),
    (["previous slide","go back","back one","prev slide"],
     {"action":"delegate","preamble":"Going back!","tool":"ppt_navigate","args":{"direction":"prev"},"mode":"queue"}),
    (["first slide","go to start","beginning"],
     {"action":"delegate","preamble":"Back to the start!","tool":"ppt_navigate","args":{"direction":"first"},"mode":"queue"}),
    (["last slide","go to end","final slide","end slide"],
     {"action":"delegate","preamble":"Jumping to the end!","tool":"ppt_navigate","args":{"direction":"last"},"mode":"queue"}),
    (["hello","hi pilot","hey pilot","good morning","good afternoon","hi there"],
     {"action":"respond_now","preamble":"Hey! I'm listening — what can I help with?","tool":None,"args":{},"mode":"queue"}),
    (["thank you","thanks","great","good job","well done"],
     {"action":"respond_now","preamble":"Happy to help! What else can I do?","tool":None,"args":{},"mode":"queue"}),
    (["our policy","company policy","internal","kb search","policy on","procedure for"],
     {"action":"delegate","preamble":"Let me check that.","tool":"kb_search","args":{},"mode":"queue"}),
    (["tell me","who is","what is","who was","what was","explain","define","how does","why does","when did","where is","when is"],
     {"action":"delegate","preamble":"Let me find that out!","tool":"general_qa","args":{},"mode":"queue"}),
    (["create ticket","open ticket","new ticket","raise ticket","log issue"],
     {"action":"delegate","preamble":"Creating that ticket now.","tool":"ticket_create","args":{},"mode":"queue"}),
    (["summarize","summary of","overview of","what is this presentation","what's this about","describe the presentation","summarise"],
     {"action":"delegate","preamble":"Let me summarize the presentation for you!","tool":"ppt_summarize","args":{},"mode":"queue"}),
    (["delete slide","delete this slide","remove slide","remove this slide","delete current slide"],
     {"action":"delegate","preamble":None,"tool":"ppt_delete_slide","args":{},"mode":"queue"}),
    (["book flight","find flight","search flight","fly to","flights from"],
     {"action":"delegate","preamble":"Checking flights for you!","tool":"flight_search","args":{},"mode":"queue"}),
    (["look up customer","find customer","customer details","crm"],
     {"action":"delegate","preamble":"Looking up that customer.","tool":"crm_lookup","args":{},"mode":"queue"}),
]


# ── Per-session conversation memory ─────────────────────────────────────────

class ConversationMemory:
    """Rolling message history for one session (adapted from core/llm.py)."""
    MAX_TURNS = 10  # keep last 10 exchanges (20 messages)

    def __init__(self) -> None:
        self._history: list[dict] = []

    def add(self, role: str, content: str) -> None:
        self._history.append({"role": role, "content": content})
        if len(self._history) > self.MAX_TURNS * 2:
            self._history = self._history[-(self.MAX_TURNS * 2):]

    def messages(self) -> list[dict]:
        return [{"role": "system", "content": SYSTEM_PROMPT}] + self._history

    def clear(self) -> None:
        self._history.clear()


_memories: dict[str, ConversationMemory] = {}


def _get_memory(session_id: str) -> ConversationMemory:
    if session_id not in _memories:
        _memories[session_id] = ConversationMemory()
    return _memories[session_id]


def clear_memory(session_id: str) -> None:
    _memories.pop(session_id, None)


# ── Sync Ollama call (run in thread, same pattern as core/llm.py) ────────────

def _ollama_chat_sync(messages: list[dict]) -> str:
    """Blocking ollama.chat() — called via asyncio.to_thread() to avoid blocking the loop."""
    import ollama
    response = ollama.chat(
        model=settings.OLLAMA_MODEL,
        messages=messages,
        think=False,               # disable Qwen3 thinking — cuts ~50s off latency
        options={"num_predict": 200},  # headroom for full delegate JSON (preamble+tool+args+mode)
        stream=False,
    )
    # Support both dict and attribute access (ollama package version differences)
    if isinstance(response, dict):
        return response["message"]["content"].strip()
    return response.message.content.strip()


# ── Provider ─────────────────────────────────────────────────────────────────

class FrontLLMProvider:
    def __init__(self):
        self._available = False

    def load(self):
        try:
            import ollama, httpx
            r = httpx.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=4.0)
            available = [m["name"] for m in r.json().get("models", [])]
            if any(settings.OLLAMA_MODEL in n for n in available):
                self._available = True
                logger.info(f"Ollama ready → {settings.OLLAMA_MODEL}")
            else:
                logger.warning(
                    f"Ollama running but '{settings.OLLAMA_MODEL}' not pulled. "
                    f"Run: ollama pull {settings.OLLAMA_MODEL}  "
                    f"(available: {available})"
                )
        except Exception as e:
            logger.warning(f"Ollama not reachable at {settings.OLLAMA_BASE_URL}: {e} — keyword fallback active")

    async def classify(self, text: str, speaker_id: str, role: str,
                       context: list, usecase: str = "general",
                       session_id: str = "") -> dict:
        # Fast path: slide number is deterministic — bypass Ollama to avoid 1-vs-0 index confusion.
        # IMPORTANT: skip fast path if user said "delete/remove" — those must reach ppt_delete_slide.
        if usecase != "customercare":
            _tl = text.lower()
            _is_destructive = any(w in _tl for w in ("delete", "remove"))
            if not _is_destructive:
                _m = re.search(r'\bslide[s]?\s+(\d+)\b', _tl)
                if _m:
                    num = int(_m.group(1))
                    return {"action": "delegate", "preamble": f"Going to slide {num}!",
                            "tool": "ppt_jump_to_title",
                            "args": {"query": text, "slide_number": num - 1},
                            "mode": "queue"}

        if self._available:
            try:
                ctx_str = "\n".join(
                    f"{c.get('speaker','?')}: {c.get('text','')}"
                    for c in context[-5:]
                )
                user_msg = (
                    f"Today's date: {datetime.date.today().isoformat()}\n"
                    f"Usecase: {usecase}\n"
                    f"Recent context:\n{ctx_str}\n\n"
                    f"Speaker: {speaker_id} ({role})\n"
                    f'Text: "{text}"\n'
                    "Classify:"
                )

                mem = _get_memory(session_id) if session_id else None
                if mem:
                    mem.add("user", user_msg)
                    messages = mem.messages()
                else:
                    messages = [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": user_msg},
                    ]

                raw = await asyncio.to_thread(_ollama_chat_sync, messages)

                result = json.loads(_extract_json(raw))

                # If a tool is named, action MUST be delegate — model sometimes
                # returns respond_now + tool which causes the tool to be silently skipped.
                if result.get("tool") and result.get("action") != "delegate":
                    result["action"] = "delegate"

                # Guard: model sometimes fakes answers in preamble and uses respond_now
                # for knowledge questions. Detect question-shaped inputs and force general_qa.
                result = _fix_respond_now_questions(result, text, usecase)

                if mem:
                    mem.add("assistant", raw)

                logger.info(f"Ollama classified: action={result.get('action')} tool={result.get('tool')}")
                return self._fill_args(result, text)

            except json.JSONDecodeError:
                logger.error(f"Ollama returned non-JSON: {raw!r}")
            except Exception as e:
                logger.error(f"Ollama classify error: {e}")

        return self._keyword_fallback(text, usecase)

    def _fill_args(self, result: dict, text: str) -> dict:
        tool = result.get("tool")
        args = result.get("args") or {}
        if tool == "kb_search" and not args.get("query"):
            args["query"] = text
        if tool == "ticket_create" and not args.get("synopsis"):
            args["synopsis"] = text
            args.setdefault("category", "general")
            args.setdefault("symptoms", "")
        if tool == "ppt_jump_to_title" and not args.get("query"):
            args["query"] = text
        if tool == "flight_search":
            # Strip trailing punctuation so "from Chennai to Mumbai." works
            t = re.sub(r'[.,!?]+$', '', text.lower().strip())
            # Extract "from X to Y"
            m = re.search(
                r'from\s+([a-zA-Z ]+?)\s+to\s+([a-zA-Z ]+?)(?:\s+on\b|\s+for\b|\s+tomorrow\b|\s+today\b|\s*$)',
                t
            )
            if m:
                args.setdefault("origin",      m.group(1).strip())
                args.setdefault("destination", m.group(2).strip())
            else:
                # "flights to Mumbai"
                m2 = re.search(r'\bto\s+([a-zA-Z ]+?)(?:\s+on\b|\s+from\b|\s*$)', t)
                if m2:
                    args.setdefault("destination", m2.group(1).strip())
            # Date extraction — explicit dates (ISO, "July 17", bare "10th") win
            # over vague tomorrow/today keywords. Otherwise a correction like
            # "not today, I want the 10th" would match "today" as a substring
            # and never even look for the actual date the user asked for.
            # NOTE: these overwrite (not setdefault) any date the LLM produced —
            # Qwen3 has no reliable sense of the real current date/year and has
            # been observed hallucinating stale years (e.g. "2023") even when
            # told the correct day/month, so the deterministic parser always wins.
            dm = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', text)
            if dm:
                args["date"] = dm.group(1)
            else:
                nd = _parse_natural_date(text)
                if nd:
                    args["date"] = nd
                elif "tomorrow" in t:
                    args["date"] = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
                elif "today" in t:
                    args["date"] = datetime.date.today().isoformat()
        result["args"] = args
        return result

    def _keyword_fallback(self, text: str, usecase: str = "general") -> dict:
        t = text.lower().strip()
        if len(t) < 3:
            return {"action": "ignore", "preamble": None, "tool": None, "args": {}, "mode": "queue"}

        ppt_only  = usecase == "ppt"
        care_only = usecase == "customercare"

        if not care_only:
            # "delete slide 10" / "remove slide 3" — must be checked BEFORE generic slide-N navigation
            del_m = re.search(r'\b(?:delete|remove)\b.*?\bslide[s]?\s+(\d+)\b', t)
            if del_m:
                num = int(del_m.group(1))
                return {"action": "delegate", "preamble": None,
                        "tool": "ppt_delete_slide",
                        "args": {"slide_number": num - 1},
                        "mode": "queue"}
            # Generic slide number → navigate
            m = re.search(r'\bslide[s]?\s+(\d+)\b', t)
            if m:
                num = int(m.group(1))
                return {"action": "delegate", "preamble": f"Going to slide {num}!",
                        "tool": "ppt_jump_to_title",
                        "args": {"query": text, "slide_number": num - 1},
                        "mode": "queue"}

        _CARE_TOOLS = {"ticket_create","ticket_update","ticket_close","kb_search","crm_lookup","flight_search","flight_book"}

        for keywords, response in _KEYWORDS:
            tool = response.get("tool", "")
            if ppt_only  and tool and not tool.startswith("ppt"):
                continue
            if care_only and tool and tool.startswith("ppt"):
                continue
            if care_only and response.get("action") == "respond_now":
                continue  # ignore social greetings in care mode
            if care_only and tool and tool not in _CARE_TOOLS:
                continue  # ignore general_qa and other non-care tools in care mode
            if any(k in t for k in keywords):
                r = response.copy()
                r["args"] = dict(response["args"])
                if r.get("tool") == "kb_search":
                    r["args"]["query"] = text
                if r.get("tool") == "ticket_create":
                    r["args"] = {"category": "general", "synopsis": text, "symptoms": ""}
                if r.get("tool") == "flight_search":
                    r = self._fill_args(r, text)
                return r

        if care_only:
            return {"action": "ignore", "preamble": None, "tool": None, "args": {}, "mode": "queue"}

        if len(t.split()) >= 3:
            return {"action": "delegate", "preamble": "Let me think about that!",
                    "tool": "general_qa", "args": {"query": text}, "mode": "queue"}

        return {"action": "ignore", "preamble": None, "tool": None, "args": {}, "mode": "queue"}


front_llm_provider = FrontLLMProvider()
