"""
Smart Turn — end-of-turn detector (linguistic heuristic implementation).

Classifies each ASR segment as "complete turn" (route to LLM) or "incomplete turn"
(wait for more audio). Uses a vocabulary of dangling tail words (conjunctions, articles,
modals, fillers) plus punctuation detection.

Usage:
    from services.smart_turn import smart_turn
    complete, confidence = smart_turn.check("and next I want to walk through")
    # → (False, 0.84)  — speaker hasn't finished yet
"""
import logging
logger = logging.getLogger("pilot.smart_turn")

# Words that almost always indicate an incomplete turn when they're the last word
_INCOMPLETE_TAIL = {
    # Conjunctions / subordinators
    "and", "but", "or", "because", "so", "that", "which", "who",
    "when", "where", "while", "although", "if", "then", "though",
    # Articles / prepositions (dangling)
    "the", "a", "an", "to", "of", "in", "with", "for", "by", "at",
    "on", "as", "from", "into", "about", "over", "after", "before",
    # Pronouns starting a new clause (not "it"/"this" — too common as command objects)
    "i", "we", "you", "they", "my", "our", "their", "his", "her", "its",
    # Modals / auxiliaries mid-clause ("I think we should ___")
    "should", "would", "could", "will", "can", "must", "might", "may",
    "need", "want", "going",
    # Fillers
    "uh", "um", "like", "also", "just", "even", "only",
}


def _heuristic(text: str) -> tuple[bool, float]:
    """Linguistic heuristic for turn completion. Returns (is_complete, confidence)."""
    t = text.strip()
    if not t:
        return True, 1.0

    tl = t.lower()

    # Explicit end punctuation → almost certainly done
    if tl[-1] in ".!?":
        return True, 0.92

    words = tl.split()

    # Check last word BEFORE the short-utterance check so "because the" (2 words)
    # still triggers the incomplete path
    last = words[-1].rstrip(",:;")
    if last in _INCOMPLETE_TAIL:
        return False, 0.84

    # Very short utterances with no dangling tail: single commands ("next", "stop",
    # "go back") → treat as complete
    if len(words) <= 2:
        return True, 0.80

    return True, 0.65


class SmartTurnProvider:
    def __init__(self):
        self._classifier = None
        self._label_complete: str | None = None
        self._loaded = False

    def load(self):
        """Lazy load — called once on first use."""
        if self._loaded:
            return
        self._loaded = True
        self._try_load_model()

    def _try_load_model(self):
        # pipecat-ai/smart-turn uses a custom inference format (not standard transformers
        # tokenizer pipeline) — attempting to load it via pipeline() always fails with
        # NoneType tokenizer errors. The linguistic heuristic below is accurate and instant,
        # so we skip the network round-trip and use it directly.
        logger.info("Smart Turn: using linguistic heuristic (fast, no model download needed)")
        self._classifier = None

    def check(self, text: str) -> tuple[bool, float]:
        """
        Returns (is_complete_turn, confidence).
        True  → route to Front LLM now.
        False → wait for more audio (speaker still mid-sentence).
        """
        if not self._loaded:
            self.load()

        if self._classifier is not None:
            try:
                results = self._classifier(text.strip()[:512])
                # results = [{"label": "COMPLETE", "score": 0.87}, {"label": "INCOMPLETE", "score": 0.13}]
                for r in results:
                    if r["label"].upper() == self._label_complete:
                        return True, round(r["score"], 3)
                # If label_complete not found, take the highest-scoring label
                best = max(results, key=lambda r: r["score"])
                is_complete = best["label"].upper() == self._label_complete
                return is_complete, round(best["score"], 3)
            except Exception as e:
                logger.warning(f"Smart Turn inference error ({e}) — heuristic fallback")

        return _heuristic(text)


smart_turn = SmartTurnProvider()
