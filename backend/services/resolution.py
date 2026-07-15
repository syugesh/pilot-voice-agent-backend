"""
Resolution-confidence + escalation-recommendation engine.

Given a live call's transcript, its running sentiment history, and the KB hits
retrieved for the issue, decides how likely the CSR is to resolve this on the
current call vs. whether it should be escalated — and, critically, *why*.

Design: a deterministic RULE layer runs first and sets a hard floor (the
explainable, auditable "these facts require escalation" logic from the plan's
decision factors — repeated failures, long outage, sustained high frustration,
weak KB match). Then an LLM reasoning pass (shared Ollama→Gemini→Groq chain)
writes the human-readable reasoning and can *raise* confidence, but can never
silently override a hard-escalate rule — so the recommendation stays trustworthy
even when the LLM is optimistic. Falls back to rules-only if all LLMs fail.
"""
import json, logging, re
logger = logging.getLogger("pilot.resolution")

_DURATION_RE = re.compile(r'\b(\d+)\s*(day|days|week|weeks)\b', re.I)
_REBOOT_RE = re.compile(r'\b(reboot|restart|reset|power\s*cycle)\b', re.I)
_PRIOR_CONTACT_RE = re.compile(r'\b(call(ed)?|contact(ed)?|spoke|chat(ted)?)\b.*\b(twice|three times|multiple|several|again|before|earlier|yesterday)\b', re.I)

RESOLUTION_PROMPT = """You are assisting a customer-support rep on a live call.
Given the issue and the deterministic signals below, write a JSON object with:
- "reasoning": 1-2 plain sentences explaining the resolution outlook for the rep.
- "resolution_confidence": a number 0.0-1.0 (how likely THIS call resolves the issue now).
Return ONLY the JSON object, no markdown, no other text. Base it strictly on the
signals given; do not invent facts."""


def _rule_layer(transcript_text: str, sentiment_history: list[dict], kb_hits: list[dict]) -> dict:
    """Deterministic signals + hard-escalate floor. Returns partial assessment
    the LLM pass then explains/refines."""
    t = transcript_text.lower()
    reasons: list[str] = []

    # Outage duration
    long_outage = False
    for m in _DURATION_RE.finditer(transcript_text):
        n, unit = int(m.group(1)), m.group(2).lower()
        days = n * 7 if unit.startswith("week") else n
        if days >= 1:
            long_outage = True
            reasons.append(f"Outage reported as {n} {unit} (>24h).")
            break

    # Repeated self-remediation already attempted
    reboot_mentions = len(_REBOOT_RE.findall(transcript_text))
    repeated_reboots = reboot_mentions >= 1 and bool(re.search(r'\b(multiple|several|twice|three|again|still|keep)\b', t))
    if repeated_reboots:
        reasons.append("Customer already attempted reboot/reset without success.")

    # Prior support contacts
    prior_contacts = bool(_PRIOR_CONTACT_RE.search(transcript_text))
    if prior_contacts:
        reasons.append("Customer has contacted support about this before.")

    # Sustained high frustration (any recent customer turn at/over threshold)
    recent = sentiment_history[-5:] if sentiment_history else []
    max_frustration = max((s.get("frustration_score", 0.0) for s in recent), default=0.0)
    high_frustration = max_frustration >= 0.7
    if high_frustration:
        reasons.append(f"Customer frustration is high ({max_frustration:.0%}).")

    # KB coverage — a weak top hit means L1 has no confident procedure to follow
    top_kb = kb_hits[0]["score"] if kb_hits else 0.0
    weak_kb = top_kb < 0.35
    if weak_kb:
        reasons.append("No strongly-matching knowledge-base procedure found.")

    # Hard-escalate floor: any two independent escalation signals, OR a single
    # decisive one (long outage after reboots), forces escalate regardless of
    # what the LLM concludes.
    escalation_signals = sum([long_outage, repeated_reboots, prior_contacts, high_frustration])
    force_escalate = escalation_signals >= 2 or (long_outage and repeated_reboots)

    # A confidence ceiling the LLM cannot exceed when signals are stacking up.
    if force_escalate:
        conf_ceiling = 0.35
    elif escalation_signals == 1:
        conf_ceiling = 0.6
    else:
        conf_ceiling = 1.0

    return {
        "reasons": reasons,
        "force_escalate": force_escalate,
        "conf_ceiling": conf_ceiling,
        "signals": {
            "long_outage": long_outage, "repeated_reboots": repeated_reboots,
            "prior_contacts": prior_contacts, "high_frustration": high_frustration,
            "weak_kb": weak_kb, "top_kb_score": round(top_kb, 3),
        },
    }


def _escalation_target(signals: dict) -> str:
    # Line/sync problems go to L2 technicians; everything else defaults to L2 support.
    return "L2 Technician" if (signals.get("long_outage") or signals.get("repeated_reboots")) else "L2 Support"


async def assess(transcript_text: str, sentiment_history: list[dict], kb_hits: list[dict]) -> dict:
    rules = _rule_layer(transcript_text, sentiment_history, kb_hits)

    # LLM reasoning pass (explains + may raise confidence within the ceiling)
    signal_lines = "\n".join(f"- {r}" for r in rules["reasons"]) or "- No escalation signals detected."
    kb_line = kb_hits[0]["title"] if kb_hits else "none"
    content = (
        f"Issue transcript:\n{transcript_text[:1500]}\n\n"
        f"Deterministic signals:\n{signal_lines}\n"
        f"Best knowledge-base match: {kb_line} (score {rules['signals']['top_kb_score']}).\n\n"
        "Assess the resolution outlook."
    )

    llm_conf = None
    reasoning = None
    try:
        from backend.services.session_summary import run_llm_chain
        raw = await run_llm_chain(RESOLUTION_PROMPT, content, max_tokens=220)
        if raw:
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                parsed = json.loads(m.group(0))
                reasoning = (parsed.get("reasoning") or "").strip() or None
                c = parsed.get("resolution_confidence")
                if isinstance(c, (int, float)):
                    llm_conf = max(0.0, min(1.0, float(c)))
    except Exception as e:
        logger.warning(f"Resolution LLM pass failed ({e}) — rules-only")

    # Combine: start from LLM confidence if present, else a rules baseline;
    # then clamp to the ceiling the rules imposed (LLM can't override escalation).
    baseline = 0.75 if not rules["reasons"] else 0.45
    confidence = llm_conf if llm_conf is not None else baseline
    confidence = min(confidence, rules["conf_ceiling"])

    recommendation = "escalate" if (rules["force_escalate"] or confidence < 0.4) else "resolve"

    if not reasoning:
        reasoning = (
            "Multiple escalation signals detected — recommend routing to a specialist."
            if recommendation == "escalate"
            else "Issue appears resolvable on this call with standard troubleshooting."
        )

    return {
        "resolution_confidence": round(confidence, 3),
        "recommendation": recommendation,
        "reasoning": reasoning,
        "escalation_target": _escalation_target(rules["signals"]) if recommendation == "escalate" else None,
        "escalation_reasons": rules["reasons"],
        "signals": rules["signals"],
    }
