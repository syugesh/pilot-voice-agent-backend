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
from backend.core.config import settings

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

_WORD_NUMS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}


_ROUTE_MATCH_RE = re.compile(
    r'\b(?:go\s*to|open|switch\s*to|navigate\s*to|show|start|join)\s+(?:the\s+)?'
    # "triplanner": ASR merges "trip planner"'s shared p/p boundary into a
    # single p, so \s* alone doesn't help — "planner" isn't even a substring
    # of "triplanner" anymore. Listed as an explicit literal alternate rather
    # than trying to generalize a fuzzy-merge rule for one observed case.
    #
    # "PPT" spoken as letters is a common Whisper mishearing target — observed
    # transcriptions include "ppd copilot", "pppt copilot", "pppd copilot",
    # "ppp copilot", and more. These all share a shape (starts with "p",
    # followed by 1-4 more p/t/d-sounding letters, then "copilot") rather
    # than being one-off substitutions like "triplanner" above, so this is a
    # fuzzy pattern (p[ptd]{1,4}) instead of an enumerated literal list —
    # enumerating every mishearing variant one at a time doesn't scale.
    r'(trip\s*planner|triplanner|travel|p[ptd]{1,4}\s*copilot|presentation|powerpoint|customer\s*resolution|'
    r'resolution|customer\s*care|meet\s*room|meeting\s*feature|meeting|talkinia|dashboard|main\s*dashboard|'
    r'home|guidelines|guideline|system\s*guidelines|profile|settings)\b'
)


def _match_navigate_page(normalized_text: str) -> dict | None:
    """Shared page-routing fast path — used both ahead of the main Ollama
    classify() call and inside _keyword_fallback (Ollama-unavailable/timeout
    path), so a destination phrase is recognized deterministically either
    way rather than only when Ollama happens to respond in time. \\s* (not
    \\s+) throughout the destination alternatives — ASR sometimes glues words
    together ("Triplanner"), and a literal \\s+ would silently fail to match."""
    route_match = _ROUTE_MATCH_RE.search(normalized_text)
    if not route_match:
        return None

    dest = route_match.group(1).replace(" ", "")
    page_id = "dashboard"
    preamble = "Going to Main Dashboard."

    if dest in ("tripplanner", "triplanner", "travel"):
        page_id = "care"
        preamble = "Going to Trip Planner."
    elif dest in ("presentation", "powerpoint") or re.match(r'^p[ptd]{1,4}copilot$', dest):
        page_id = "ppt"
        preamble = "Going to PPT Copilot."
    elif dest in ("customerresolution", "resolution", "customercare"):
        page_id = "resolution"
        preamble = "Going to Customer Resolution."
    elif dest in ("meetingfeature", "meeting", "talkinia", "meetroom"):
        page_id = "meetings"
        preamble = "Going to MeetRoom."
    elif dest in ("guideline", "guidelines", "systemguidelines"):
        page_id = "guideline"
        preamble = "Going to System Guidelines."
    elif dest == "profile":
        page_id = "profile"
        preamble = "Going to Profile Page."
    elif dest == "settings":
        page_id = "settings"
        preamble = "Going to Settings Page."

    return {"action": "delegate", "preamble": preamble,
            "tool": "navigate_page", "args": {"page": page_id},
            "mode": "queue"}


def _normalize_spoken_numbers(t: str) -> str:
    """'slide number one' -> 'slide number 1' — ASR/keyword-fallback regexes
    below only match digits, so a spelled-out number would otherwise silently
    fail to match at all (falls through to the generic/wrong branch instead
    of the intended slide-targeted one)."""
    for word, num in _WORD_NUMS.items():
        t = re.sub(rf"\b{word}\b", str(num), t)
    return t


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

PPT TOOLS:
- ppt_navigate(direction:next|prev|first|last)
- ppt_jump_to_title(query,slide_number)
- ppt_summarize()
- ppt_delete_slide(slide_number?)
- ppt_edit_slide(instruction, slide_number?) — kind-aware: pass the user's edit request
  verbatim as instruction ("make the title bold", "change the title to Project
  Overview", "add a bullet about market expansion"); the editor figures out what
  changed from the instruction text itself, no pre-parsed fields needed.
- ppt_generate_notes(slide_number?, all?) — generate speaker notes for one slide or all
- ppt_last_action() — "what did you just change?" / "did that work?"
- ppt_add_slide(instruction) — ADDS a new slide; use for "add/insert/create a slide about X",
  never for edits to an existing slide. If no position is said, PILOT picks
  one automatically for narrative flow rather than always appending at the end.
- ppt_reorder_slide(instruction) — moves an EXISTING slide, e.g. "move slide 2
  after slide 5", "move the third slide to the beginning". Never use this to
  add or edit content — only to change slide order.
# DISABLED: ppt_improvise_slide tool removed upstream (no replacement)
# - ppt_improvise_slide(prompt)

EXAMPLES (strict):
- "make the title bold" → ppt_edit_slide(instruction="make the title bold")
- "change the title to Project Overview" → ppt_edit_slide(instruction="change the title to Project Overview")
- "add a bullet point about market expansion" → ppt_edit_slide(instruction="add a bullet point about market expansion")
- "add a slide about pricing" / "insert a new slide" → ppt_add_slide(instruction="add a slide about pricing")
- "generate speaker notes for this slide" → ppt_generate_notes()
- "write notes for every slide" → ppt_generate_notes(all=true)
# DISABLED: ppt_improvise_slide tool removed upstream (no replacement)
# - "improvise this slide" / "improve the slide" / "rewrite this slide to make it sound more professional" → ppt_improvise_slide(prompt="rewrite this slide to make it sound more professional")

CARE TOOLS: ticket_create, ticket_update, ticket_close, kb_search(query), crm_lookup, flight_search, flight_book
RESOLUTION TOOLS: resolution_assess(), escalate_ticket(synopsis?, category?, symptoms?, priority?, escalation_target?)

TRIP PLANNER — flight_search(service_type, origin, destination, date, min_rating?, query) handles
FLIGHTS, HOTELS, and TRAINS — it is one tool covering all three, distinguished by service_type:
- service_type="flights": needs origin + destination + date.
- service_type="hotels": needs origin (the city/area to search — "near my location" is a valid
  origin, the tool resolves real GPS coordinates separately) + date. destination is not used.
  If a star rating is mentioned ("5 star hotels", "4-star and up"), set min_rating to that number.
- service_type="trains": needs origin + destination + date. If there's no direct route the tool
  finds a real connecting route through an interchange city — you don't need to worry about that,
  just pass the two cities asked for.
Always include the ORIGINAL verbatim user sentence as "query" in args, in addition to whatever
structured fields you extract — the tool uses it as a fallback for anything you didn't catch.
Only set date to an ISO date (YYYY-MM-DD) if you can confidently resolve one from "today"/
"tomorrow"/an explicit date; otherwise omit it and let the tool default it.

TRIP PLANNER EXAMPLES (strict):
- "search flights from Mumbai to Delhi" →
  flight_search(service_type="flights", origin="Mumbai", destination="Delhi", query="search flights from Mumbai to Delhi")
- "find me 5 star hotels in Paris" →
  flight_search(service_type="hotels", origin="Paris", min_rating=5, query="find me 5 star hotels in Paris")
- "any hotels near my location" →
  flight_search(service_type="hotels", origin="near my location", query="any hotels near my location")
- "search trains from London to Manchester" →
  flight_search(service_type="trains", origin="London", destination="Manchester", query="search trains from London to Manchester")
Do NOT default an ambiguous travel query to service_type="flights" — if the word "hotel", "stay",
"room", or "lodging" appears, it's hotels; if "train", "rail", or "railway" appears, it's trains.
GENERAL: general_qa(query) — world knowledge, facts, concepts, definitions, science, "who is", "what is", "tell me", "explain", "how does", "why", "what does", "describe", "difference between"

slide_number is 0-indexed: "go to slide 7" → slide_number=6
Use ppt_summarize when user asks to summarize, overview, or describe the presentation.
Use ppt_edit_slide when user asks to change, update, edit, or format the text/style on an EXISTING slide.
Use ppt_add_slide when user asks to add, insert, or create a NEW slide.
Use general_qa for ANY factual or conceptual question — including questions about things shown on slides.
Use kb_search ONLY for company-internal policies, procedures, or internal documents — NOT general world knowledge.
Use resolution_assess when the rep asks whether to escalate, what to do, how the call is going, or for a
resolution/escalation recommendation ("should I escalate this?", "what's the recommendation?", "how likely
can I resolve this?"). Use escalate_ticket to create an escalation ticket for the current issue.
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
     {"action":"delegate","preamble":"Checking flights for you!","tool":"flight_search","args":{"service_type":"flights"},"mode":"queue"}),
    (["hotel","hotels","room","stay","lodging","place to stay"],
     {"action":"delegate","preamble":"Checking hotels for you!","tool":"flight_search","args":{"service_type":"hotels"},"mode":"queue"}),
    (["train","trains","rail","railway"],
     {"action":"delegate","preamble":"Checking trains for you!","tool":"flight_search","args":{"service_type":"trains"},"mode":"queue"}),
    (["look up customer","find customer","customer details","crm"],
     {"action":"delegate","preamble":"Looking up that customer.","tool":"crm_lookup","args":{},"mode":"queue"}),
    (["make title bold", "bold title", "make the title bold"],
     {"action":"delegate","preamble":"Making the title bold!","tool":"ppt_edit_slide","args":{"instruction":"make the title bold"},"mode":"queue"}),
    (["make title italic", "italic title", "italicize title"],
     {"action":"delegate","preamble":"Italicizing the title!","tool":"ppt_edit_slide","args":{"instruction":"make the title italic"},"mode":"queue"}),
    (["make bullets bold", "bold bullets", "make the bullets bold"],
     {"action":"delegate","preamble":"Making bullets bold!","tool":"ppt_edit_slide","args":{"instruction":"make the bullets bold"},"mode":"queue"}),
    (["make bullets italic", "italic bullets", "italicize bullets"],
     {"action":"delegate","preamble":"Italicizing bullets!","tool":"ppt_edit_slide","args":{"instruction":"make the bullets italic"},"mode":"queue"}),
    (["reset title style", "make title normal"],
     {"action":"delegate","preamble":"Resetting title style.","tool":"ppt_edit_slide","args":{"instruction":"reset the title style to normal"},"mode":"queue"}),
    (["reset bullets style", "make bullets normal"],
     {"action":"delegate","preamble":"Resetting bullets style.","tool":"ppt_edit_slide","args":{"instruction":"reset the bullets style to normal"},"mode":"queue"}),
    # DISABLED: ppt_improvise_slide tool removed upstream (no replacement)
    # (["improvise slide", "improvise this slide", "improve slide", "improve this slide"],
    #  {"action":"delegate","preamble":"Improvising this slide for you!","tool":"ppt_improvise_slide","args":{"prompt":"improve this slide"},"mode":"queue"}),
    (["generate notes", "generate speaker notes", "write notes", "create speaker notes"],
     {"action":"delegate","preamble":"Generating speaker notes!","tool":"ppt_generate_notes","args":{},"mode":"queue"}),
    (["what did you change", "what did you just change", "did that work", "did it work"],
     {"action":"delegate","preamble":None,"tool":"ppt_last_action","args":{},"mode":"queue"}),
    (["should i escalate","should we escalate","recommend escalation","what's the recommendation",
      "what is the recommendation","assess this","resolution recommendation","escalate or resolve",
      "can i resolve this","how's this call going","how is this call going"],
     {"action":"delegate","preamble":"Assessing this call.","tool":"resolution_assess","args":{},"mode":"queue"}),
    (["escalate this ticket","create an escalation","raise an escalation","escalate to l2"],
     {"action":"delegate","preamble":"Creating an escalation ticket.","tool":"escalate_ticket","args":{},"mode":"queue"}),
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

_ollama_client = None

def _get_ollama_client():
    # A bounded-timeout client, not the bare ollama.chat() convenience function —
    # that uses a default client with NO request timeout, so a slow/degraded
    # Ollama (e.g. under system memory pressure) hangs classify() indefinitely
    # instead of failing fast into the deterministic keyword fallback.
    global _ollama_client
    if _ollama_client is None:
        import ollama
        _ollama_client = ollama.Client(host=settings.OLLAMA_BASE_URL, timeout=settings.OLLAMA_TIMEOUT_S)
    return _ollama_client


def _ollama_chat_sync(messages: list[dict]) -> str:
    """Blocking ollama.chat() — called via asyncio.to_thread() to avoid blocking the loop."""
    client = _get_ollama_client()
    response = client.chat(
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


_REPOLISH_PROMPT = """You clean up raw speech-to-text transcripts of voice commands before they're
routed to an AI assistant's tool-selection engine. The engine sometimes misreads a
run-on, multi-sentence, or awkwardly-phrased transcript as a plain acknowledgment
instead of the real request buried inside it — your job is to prevent that.

Rewrite the transcript below into ONE clear, grammatically correct instruction, by:
- Understanding what the speaker actually wants overall, then merging any
  run-on fragments or separate sentences that describe ONE request into a
  single coherent command (e.g. "go to slide six. read the notes out loud."
  -> "go to slide six and read the notes out loud").
- Removing a wake-word address at the start ("hey pilot", "okay pilot", "pilot,", etc)
  — it's not part of the command.
- Fixing obvious speech-to-text grammar, word-order, or filler-word mistakes.

Rules (do not break these):
- Preserve EVERY concrete detail exactly: place names, numbers, dates, ratings,
  quoted text. Never drop, invent, guess, or change any of them.
- Do NOT answer the request. Do NOT add information that wasn't said.
- If the transcript is already a single clear command, return it unchanged.
- Output ONLY the rewritten command text, nothing else — no quotes, no preamble.

Usecase: {usecase}
Transcript: "{text}"

Rewritten command:"""


def _repolish_utterance_sync(text: str, usecase: str) -> str:
    """Blocking Ollama call — see _repolish_utterance for the guard rail that
    makes this safe to use on the hot path."""
    client = _get_ollama_client()
    prompt = _REPOLISH_PROMPT.format(usecase=usecase or "general", text=text)
    response = client.chat(
        model=settings.OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        think=False,
        options={"num_predict": 120},
        stream=False,
    )
    content = response["message"]["content"] if isinstance(response, dict) else response.message.content
    return content.strip().strip('"')


async def _repolish_utterance(text: str, usecase: str) -> str:
    """Semantic pre-pass that repolishes a raw voice transcript into one clean,
    syntactically/semantically correct command before it ever reaches
    classify()'s Ollama call — a run-on or awkwardly split transcript (e.g.
    "Okay, pilot search hotels near my location. Hotel should be 5 star.") was
    otherwise sometimes misread as a plain acknowledgment instead of a real
    delegate-worthy request. Same guard-rail spirit as _polish_travel_query in
    flight_booking.py: this can only reshape phrasing, never actually change
    what was asked — any suspicious rewrite (empty, wildly longer, or missing
    most of the original's real words) is rejected and the original text is
    used as-is, so a bad rewrite can only cost the pass, never corrupt intent.
    """
    if not text or not text.strip():
        return text
    try:
        from backend.core.llm_gate import ollama_gate
        polished = await ollama_gate.run(
            lambda: _repolish_utterance_sync(text, usecase), priority="high", label="utterance_repolish"
        )
        polished = (polished or "").strip()
        if not polished:
            return text
        if len(polished) > len(text) * 2 + 20:
            # Plausibly the model answered/explained instead of rewriting.
            return text
        # Coverage guard: most of the original's real (3+ letter) words should
        # still appear in the rewrite — catches the model dropping a clause
        # instead of just re-phrasing it.
        orig_words = [w for w in re.findall(r"[a-zA-Z]+", text.lower()) if len(w) >= 3]
        if orig_words:
            polished_l = polished.lower()
            kept = sum(1 for w in orig_words if w in polished_l)
            if kept / len(orig_words) < 0.6:
                return text
        return polished
    except Exception as e:
        logger.warning(f"[utterance repolish] failed, keeping original: {e}")
        return text


# ── Provider ─────────────────────────────────────────────────────────────────

class FrontLLMProvider:
    def __init__(self):
        self._available = False

    def load(self):
        try:
            import ollama, httpx
            r = httpx.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=4.0)
            available = [m["name"] for m in r.json().get("models", [])]
            primary = settings.OLLAMA_MODEL
            if any(primary in n for n in available):
                self._available = True
                logger.info(f"Ollama ready → {settings.OLLAMA_MODEL} (primary)")
            elif any(settings.OLLAMA_FALLBACK_MODEL in n for n in available):
                # Primary not pulled yet (e.g. still downloading) — swap the
                # setting itself so every OLLAMA_MODEL call site in the app
                # transparently uses the fallback until the primary is pulled
                # and the backend is restarted.
                settings.OLLAMA_MODEL = settings.OLLAMA_FALLBACK_MODEL
                self._available = True
                logger.warning(
                    f"Primary model '{primary}' not pulled yet — using fallback "
                    f"'{settings.OLLAMA_MODEL}' instead. Run: ollama pull {primary}  "
                    f"(then restart the backend to switch back to it)"
                )
            else:
                logger.warning(
                    f"Ollama running but neither '{primary}' (primary) nor "
                    f"'{settings.OLLAMA_FALLBACK_MODEL}' (fallback) is pulled. "
                    f"Run: ollama pull {primary}  (available: {available})"
                )
        except Exception as e:
            logger.warning(f"Ollama not reachable at {settings.OLLAMA_BASE_URL}: {e} — keyword fallback active")

    async def classify(self, text: str, speaker_id: str, role: str,
                       context: list, usecase: str = "general",
                       session_id: str = "") -> dict:
        _tl = _normalize_spoken_numbers(text.lower().strip())  # "slide number one" -> "slide number 1"

        # Mid-clarification: ppt_add_slide asked "what should the new slide
        # be about?" and is waiting for the answer on this very next turn.
        # Route the whole utterance straight back to it rather than letting
        # it get independently classified — otherwise an answer like "our
        # Q4 roadmap" (no tool-shaped phrasing at all) falls through to
        # general_qa or gets ignored, and the pending state never resolves.
        if session_id:
            from backend.core.session_state import get_state
            if get_state(session_id).pending_add_slide is not None:
                return {"action": "delegate", "preamble": None,
                        "tool": "ppt_add_slide", "args": {"instruction": text}, "mode": "queue"}

        # Voice routing fast path — see _match_navigate_page.
        nav_result = _match_navigate_page(_tl)
        if nav_result:
            return nav_result

        # Fast path: slide number is deterministic — bypass Ollama to avoid 1-vs-0 index confusion.
        # IMPORTANT: skip fast path if user said "delete/remove" — those must reach ppt_delete_slide.
        if usecase != "customercare":
            _is_destructive = any(w in _tl for w in ("delete", "remove"))

            # "add/insert/create a slide about X" — checked before the generic
            # slide-number fast path below, otherwise "add slide 3 about
            # pricing" would match the bare slide-number regex and misroute to
            # navigation instead of actually adding a slide.
            _is_add_slide = (
                "notes" not in _tl
                and re.search(r'\b(?:add|insert|create)\b', _tl) is not None
                and "slide" in _tl
            )
            if _is_add_slide:
                return {"action": "delegate", "preamble": "Adding a new slide!",
                        "tool": "ppt_add_slide", "args": {"instruction": text}, "mode": "queue"}

            # "generate/write/create notes [for slide N / for every slide]"
            _is_notes = "notes" in _tl and any(w in _tl for w in ("generate", "create", "write", "add"))
            if _is_notes:
                notes_args: dict = {}
                if re.search(r'\ball\s+slides?\b|\bevery\s+slide\b|\beach\s+slide\b', _tl):
                    notes_args["all"] = True
                else:
                    _sn = re.search(r'\bslide[s]?\s+(?:number\s+)?(\d+)\b', _tl)
                    if _sn:
                        notes_args["slide_number"] = int(_sn.group(1)) - 1
                preamble = "Generating speaker notes for every slide!" if notes_args.get("all") else "Generating speaker notes!"
                return {"action": "delegate", "preamble": preamble,
                        "tool": "ppt_generate_notes", "args": notes_args, "mode": "queue"}

            # "summarize [the presentation/slide]" — deterministic, bypasses
            # Ollama entirely. Without this, the classifier occasionally
            # answers with a fake "Okay, I'll provide a summary..."
            # acknowledgment under respond_now instead of actually
            # delegating to ppt_summarize — _fix_respond_now_questions only
            # catches question-shaped or >10-word fakes, and this phrasing
            # is neither, so it slipped through with nothing ever spoken
            # beyond the acknowledgment itself.
            _is_summarize = bool(re.search(
                r'\b(?:summarize|summarise|summary of|overview of|'
                r'what(?:\'s| is) this (?:presentation|slide|about)|'
                r'describe (?:the|this) (?:presentation|slide))\b', _tl
            ))
            if _is_summarize:
                return {"action": "delegate", "preamble": "Let me summarize that for you!",
                        "tool": "ppt_summarize", "args": {}, "mode": "queue"}

            if not _is_destructive:
                _m = re.search(r'\bslide[s]?\s+(?:number\s+)?(\d+)\b', _tl)
                if _m:
                    num = int(_m.group(1))
                    return {"action": "delegate", "preamble": f"Going to slide {num}!",
                            "tool": "ppt_jump_to_title",
                            "args": {"query": text, "slide_number": num - 1},
                            "mode": "queue"}

                # Relative slide navigation ("next/previous/first/last slide")
                # is just as deterministic as a numbered slide — no LLM
                # judgment needed. This used to only exist in
                # _keyword_fallback (Ollama-timeout-only), so whenever Ollama
                # actually responded in time it had a free hand to guess and
                # could misroute — e.g. "go to last slide" got classified as
                # ppt_last_action ("what did I just change") instead of
                # actually navigating. Checking it here, before Ollama, makes
                # it always correct regardless of whether Ollama succeeds.
                if re.search(r'\b(?:next|forward)\s+slide\b|\bgo\s+forward\b|\badvance\b', _tl):
                    return {"action": "delegate", "preamble": "Moving forward!",
                            "tool": "ppt_navigate", "args": {"direction": "next"}, "mode": "queue"}
                if re.search(r'\b(?:previous|prev|back)\s+slide\b|\bgo\s+back\b', _tl):
                    return {"action": "delegate", "preamble": "Going back!",
                            "tool": "ppt_navigate", "args": {"direction": "prev"}, "mode": "queue"}
                if re.search(r'\bfirst\s+slide\b|\bgo\s+to\s+(?:the\s+)?start\b|\bbeginning\b', _tl):
                    return {"action": "delegate", "preamble": "Back to the start!",
                            "tool": "ppt_navigate", "args": {"direction": "first"}, "mode": "queue"}
                if re.search(r'\blast\s+slide\b|\bgo\s+to\s+(?:the\s+)?end\b|\bfinal\s+slide\b|\bend\s+slide\b', _tl):
                    return {"action": "delegate", "preamble": "Jumping to the end!",
                            "tool": "ppt_navigate", "args": {"direction": "last"}, "mode": "queue"}

            # DISABLED: ppt_save_slide tool removed upstream (no replacement)
            # if any(w in _tl for w in ("save slide", "save changes", "save presentation", "save the slide", "save the presentation")):
            #     return {"action": "delegate", "preamble": "Saving slide changes!",
            #             "tool": "ppt_save_slide", "args": {}, "mode": "queue"}

            # Style fast paths (bold, italic, font family changes) — pass the
            # spoken text through as `instruction`; the kind-aware editor in
            # ppt_copilot.py parses it itself rather than expecting
            # pre-parsed style booleans.
            if "title" in _tl or "bullet" in _tl or "font" in _tl:
                # 1. Bold styling
                if "bold" in _tl:
                    if "title" in _tl:
                        return {"action": "delegate", "preamble": "Making the title bold!",
                                "tool": "ppt_edit_slide", "args": {"instruction": text}, "mode": "queue"}
                    elif "bullet" in _tl:
                        return {"action": "delegate", "preamble": "Making bullets bold!",
                                "tool": "ppt_edit_slide", "args": {"instruction": text}, "mode": "queue"}
                # 2. Italic styling
                elif "italic" in _tl or "italicize" in _tl:
                    if "title" in _tl:
                        return {"action": "delegate", "preamble": "Italicizing the title!",
                                "tool": "ppt_edit_slide", "args": {"instruction": text}, "mode": "queue"}
                    elif "bullet" in _tl:
                        return {"action": "delegate", "preamble": "Italicizing bullets!",
                                "tool": "ppt_edit_slide", "args": {"instruction": text}, "mode": "queue"}
                # 3. Regular/Normal styling
                elif "normal" in _tl or "regular" in _tl or "reset" in _tl:
                    if "title" in _tl:
                        return {"action": "delegate", "preamble": "Resetting title style to normal.",
                                "tool": "ppt_edit_slide", "args": {"instruction": text}, "mode": "queue"}
                    elif "bullet" in _tl:
                        return {"action": "delegate", "preamble": "Resetting bullets style to normal.",
                                "tool": "ppt_edit_slide", "args": {"instruction": text}, "mode": "queue"}
                # 4. Font family changes
                _font_m = re.search(r'\b(?:change|convert|set|make)\b.*?\bfont\s+(?:to\s+)?([a-zA-Z0-9 ]+)\b', _tl)
                if _font_m:
                    font_name = _font_m.group(1).strip().title()
                    return {"action": "delegate", "preamble": f"Changing font to {font_name}!",
                            "tool": "ppt_edit_slide", "args": {"instruction": text}, "mode": "queue"}

                _font_m2 = re.search(r'\buse\b\s+([a-zA-Z0-9 ]+?)\s+font\b', _tl)
                if _font_m2:
                    font_name = _font_m2.group(1).strip().title()
                    return {"action": "delegate", "preamble": f"Changing font to {font_name}!",
                            "tool": "ppt_edit_slide", "args": {"instruction": text}, "mode": "queue"}

        if self._available:
            try:
                # Semantic repolish pass: a run-on or awkwardly-split transcript
                # (e.g. two sentences glued together, or a leading "okay pilot")
                # can get misread by the classifier below as a plain
                # acknowledgment instead of the real request inside it. This
                # rewrites it into one clean command first — see
                # _repolish_utterance for the guard rail that keeps it from
                # ever changing what was actually asked.
                polished_text = await _repolish_utterance(text, usecase)
                if polished_text != text:
                    logger.info(f"[front_llm] Repolished utterance: {text!r} -> {polished_text!r}")

                ctx_str = "\n".join(
                    f"{c.get('speaker','?')}: {c.get('text','')}"
                    for c in context[-5:]
                )
                user_msg = (
                    f"Today's date: {datetime.date.today().isoformat()}\n"
                    f"Usecase: {usecase}\n"
                    f"Recent context:\n{ctx_str}\n\n"
                    f"Speaker: {speaker_id} ({role})\n"
                    f'Text: "{polished_text}"\n'
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
                result = _fix_respond_now_questions(result, polished_text, usecase)

                if mem:
                    mem.add("assistant", raw)

                logger.info(f"Ollama classified: action={result.get('action')} tool={result.get('tool')}")
                return self._fill_args(result, polished_text)

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
        if tool == "escalate_ticket" and not args.get("synopsis"):
            args["synopsis"] = text
            args.setdefault("category", "escalation")
            args.setdefault("symptoms", "")
        if tool == "ppt_jump_to_title" and not args.get("query"):
            args["query"] = text
        if tool == "flight_search":
            # Always forward the verbatim utterance as "query" — the tool's
            # own service_type/star-rating/near-me detection (flight_booking.py)
            # scans this field, and previously nothing ever populated it for
            # this tool (unlike kb_search above), so hotel/train phrasing was
            # silently invisible to that logic no matter what the LLM decided.
            args.setdefault("query", text)
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
                else:
                    # "hotels in Hyderabad" / "5 star hotels in Hyderabad" — the
                    # natural way to phrase a hotel search has no "from"/"to" at
                    # all, so without this it silently found no origin and hit
                    # the "which city?" prompt even though a real city was said.
                    # "in" means the search city itself here, i.e. origin.
                    m3 = re.search(r'\bin\s+([a-zA-Z ]+?)(?:\s+on\b|\s+for\b|\s*$)', t)
                    if m3:
                        args.setdefault("origin", m3.group(1).strip())
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

        # Page-routing phrases ("go to trip planner") — checked even in
        # customercare mode, since navigating away is always valid. This is
        # the same safety net as classify()'s pre-Ollama fast path (see
        # _match_navigate_page): if Ollama times out, this keyword fallback
        # is all that's left, and without this check a routing request would
        # otherwise fall all the way through to a general_qa non-answer.
        nav_result = _match_navigate_page(t)
        if nav_result:
            return nav_result

        if not care_only:
            t = _normalize_spoken_numbers(t)  # "slide number one" -> "slide number 1"

            # "delete slide 10" / "remove slide 3" — must be checked BEFORE generic slide-N navigation
            del_m = re.search(r'\b(?:delete|remove)\b.*?\bslide[s]?\s+(?:number\s+)?(\d+)\b', t)
            if del_m:
                num = int(del_m.group(1))
                return {"action": "delegate", "preamble": None,
                        "tool": "ppt_delete_slide",
                        "args": {"slide_number": num - 1},
                        "mode": "queue"}

            # Generic content-edit phrasing ("change the title of slide 1",
            # "add a bullet to slide 2", "update the notes on slide 3") — this
            # MUST be checked before the bare slide-number navigation regex
            # below, otherwise "slide 1" alone would match first and silently
            # misroute an edit request as plain navigation, dropping the
            # actual edit intent (this is what sent "change the title of
            # slide number one" to general_qa instead of ppt_edit_slide).
            if (
                re.search(r'\bslide[s]?\b', t)
                and re.search(r'\b(?:change|update|set|rename|edit|add|make|fix|remove|delete)\b', t)
                and re.search(r'\b(?:title|bullet|notes?|text|font|bold|italic)\b', t)
            ):
                return {"action": "delegate", "preamble": "Updating the slide!",
                        "tool": "ppt_edit_slide", "args": {"instruction": text}, "mode": "queue"}

            # Generic slide number → navigate
            m = re.search(r'\bslide[s]?\s+(?:number\s+)?(\d+)\b', t)
            if m:
                num = int(m.group(1))
                return {"action": "delegate", "preamble": f"Going to slide {num}!",
                        "tool": "ppt_jump_to_title",
                        "args": {"query": text, "slide_number": num - 1},
                        "mode": "queue"}

        _CARE_TOOLS = {"ticket_create","ticket_update","ticket_close","kb_search","crm_lookup","flight_search","flight_book","resolution_assess","escalate_ticket"}

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
                if r.get("tool") == "general_qa":
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