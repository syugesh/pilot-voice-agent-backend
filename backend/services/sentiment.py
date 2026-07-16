"""
Customer sentiment / frustration / urgency detection.

Keyword/linguistic heuristic only — no HuggingFace model load. Previously
ran cardiffnlp/twitter-roberta-base-sentiment-latest, removed since it was
unused (Customer Resolution's sentiment meter is the only caller, and that
feature is currently disabled from navigation — see GuidelinePageView.tsx)
and its weights had stopped resolving on Hugging Face (404 on
preprocessor_config.json), so every load attempt was just a wasted network
round trip before falling back to this same heuristic anyway.
"""
import asyncio, logging, re
logger = logging.getLogger("pilot.sentiment")

# Words/patterns that signal a customer is escalating regardless of raw polarity —
# "still", "again", "three days" all imply repeated failure / duration, which is
# the frustration signal a flat sentiment score misses.
_URGENCY_WORDS = {
    "urgent", "immediately", "asap", "now", "emergency", "critical",
    "unacceptable", "ridiculous", "furious", "angry", "frustrated", "fed up",
    "cancel", "refund", "complaint", "manager", "supervisor", "escalate",
}
_REPEAT_WORDS = {"still", "again", "already", "keep", "keeps", "repeatedly", "multiple", "several"}
_DURATION_RE = re.compile(r'\b(\d+)\s*(day|days|week|weeks|hour|hours|month|months)\b', re.I)


class SentimentProvider:
    def __init__(self):
        pass

    def load(self):
        pass

    def _keyword_negativity(self, text: str) -> float:
        """Fallback polarity when the model isn't available — crude but never zero-signal."""
        tl = text.lower()
        neg_hits = sum(1 for w in (_URGENCY_WORDS | {"not", "no", "won't", "can't", "broken", "down", "fail"}) if w in tl)
        return min(1.0, neg_hits * 0.25)

    def _blend(self, text: str, negativity: float) -> dict:
        tl = text.lower()
        urgency_hits = sum(1 for w in _URGENCY_WORDS if w in tl)
        repeat_hits = sum(1 for w in _REPEAT_WORDS if w in tl)
        duration_hit = 1 if _DURATION_RE.search(text) else 0
        exclaim = min(text.count("!"), 3) / 3.0
        caps = 1.0 if (len(text) > 8 and sum(c.isupper() for c in text) / max(1, len(text)) > 0.6) else 0.0

        # Frustration is the customer's *state*, not just polarity: sustained
        # negativity amplified by repetition/duration/emphasis. Weighted so the
        # model's negativity dominates but the linguistic signals can push a
        # calmly-worded but clearly-escalating turn up ("this is the third day").
        frustration = min(1.0,
            0.55 * negativity
            + 0.15 * min(1.0, urgency_hits * 0.4)
            + 0.15 * min(1.0, (repeat_hits + duration_hit) * 0.4)
            + 0.10 * exclaim
            + 0.05 * caps
        )

        if urgency_hits >= 2 or duration_hit or caps:
            urgency = "high"
        elif urgency_hits == 1 or repeat_hits:
            urgency = "medium"
        else:
            urgency = "low"

        if negativity >= 0.55:
            sentiment = "negative"
        elif negativity <= 0.30:
            sentiment = "positive"
        else:
            sentiment = "neutral"

        return {
            "sentiment": sentiment,
            "sentiment_score": round(negativity, 3),
            "frustration_score": round(frustration, 3),
            "urgency": urgency,
        }

    def analyze_sync(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            return {"sentiment": "neutral", "sentiment_score": 0.0, "frustration_score": 0.0, "urgency": "low"}
        negativity = self._keyword_negativity(text)
        return self._blend(text, negativity)

    async def analyze(self, text: str) -> dict:
        return await asyncio.to_thread(self.analyze_sync, text)


sentiment_provider = SentimentProvider()
