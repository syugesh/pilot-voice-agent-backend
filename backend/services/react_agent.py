"""
Background worker agent — an autonomous ReAct (reason → act → observe) loop.

This is the "worker" in PILOT's orchestrator-workers architecture: the Front LLM
(orchestrator/gateway) hands it a GOAL, and the agent decides its own trajectory
— thinking, calling a tool, observing the result, deciding the next step — until
the goal is met or a step cap is hit. It never emits to the user directly; it
returns a structured result the Front LLM phrases and routes (see
pipeline/front_llm.py's gateway).

Design constraints that make this safe to run against a live pipeline:
  - Every tool call goes through policy_gate.check() INSIDE the loop, so RBAC /
    destructive-confirmation apply to each autonomous action — the agent can
    never self-run a gated tool (e.g. ticket_close) without the same checks a
    voice command would face.
  - A hard step cap (MAX_STEPS) bounds latency and token spend, and stops the
    classic ReAct failure mode of looping forever.
  - Deterministic tool dispatch (exact-name match against TOOL_REGISTRY) — the
    LLM proposes an action as JSON; it never executes anything itself.
  - Degrades gracefully: if the LLM chain is unavailable, it returns a
    rules-style "couldn't act" result rather than raising into the pipeline.
"""
import json, logging, re

logger = logging.getLogger("pilot.react_agent")

# Bound the loop. Most goals resolve in 1–3 tool calls; the cap catches
# runaway reasoning without truncating legitimate multi-step work.
MAX_STEPS = 5

REACT_SYSTEM = """You are PILOT's autonomous worker agent. You accomplish a GOAL by
reasoning step by step and calling tools. On each step, respond with ONE JSON object:

  {"thought": "<brief reasoning>", "action": "<tool_name>", "args": {<tool args>}}

to call a tool, OR

  {"thought": "<brief reasoning>", "final": "<one-sentence result for the user>"}

when the goal is achieved (or genuinely cannot be). Return ONLY the JSON object —
no markdown, no prose outside it. Use only tools from the provided list. Base every
step strictly on the goal and the observations so far; never invent tool results."""


def _extract_json(raw: str) -> dict | None:
    if not raw:
        return None
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _tool_catalog(allowed: list[str]) -> str:
    # A terse, LLM-facing description of what each allowed tool does. Kept here
    # (not pulled from each tool module) so the agent's view stays small and
    # stable — the full arg schema is validated downstream by _sanitize_args.
    descriptions = {
        "kb_search":       "Search the knowledge base. args: {query}",
        "crm_lookup":      "Look up a customer account. args: {query}",
        "ticket_create":   "Create a support ticket. args: {synopsis, category, symptoms}",
        "ticket_update":   "Update an existing ticket. args: {ticket_id, status, note}",
        "resolution_assess": "Assess resolve-vs-escalate for the current call. args: {}",
        "ppt_navigate":    "Navigate slides. args: {direction: next|prev|first|last}",
        "ppt_jump_to_title": "Jump to a slide by title. args: {title}",
        "ppt_summarize":   "Summarize the current slide. args: {}",
        "general_qa":      "Answer a general question. args: {query}",
    }
    lines = [f"- {t}: {descriptions[t]}" for t in allowed if t in descriptions]
    return "\n".join(lines) or "- (no tools available)"


async def run(goal: str, *, session_id: str, speaker_id: str | None, role: str | None,
              allowed_tools: list[str]) -> dict:
    """Drive the ReAct loop to accomplish `goal`. Returns a structured result:
        {status, final, steps: [{thought, action, args, observation}], tool_results}
    `final` is the one-line outcome for the Front LLM to phrase — NOT spoken here.
    """
    from services.session_summary import run_llm_chain
    from tools.registry import TOOL_REGISTRY
    from tools.policy import policy_gate

    catalog = _tool_catalog(allowed_tools)
    transcript: list[str] = [f"GOAL: {goal}", f"Available tools:\n{catalog}"]
    steps: list[dict] = []
    tool_results: list[dict] = []

    for step_i in range(MAX_STEPS):
        content = "\n\n".join(transcript) + "\n\nRespond with the next JSON step."
        raw = await run_llm_chain(REACT_SYSTEM, content, max_tokens=260)
        decision = _extract_json(raw or "")

        if decision is None:
            logger.warning(f"[{session_id[:8]}] react: unparseable step {step_i}")
            break

        if "final" in decision:
            return {"status": "ok", "final": (decision.get("final") or "").strip(),
                    "steps": steps, "tool_results": tool_results}

        action = decision.get("action")
        args = decision.get("args") or {}
        thought = (decision.get("thought") or "").strip()

        if action not in allowed_tools or action not in TOOL_REGISTRY:
            transcript.append(f"OBSERVATION: '{action}' is not an available tool. "
                              f"Choose from the tool list or return a final answer.")
            steps.append({"thought": thought, "action": action, "args": args,
                          "observation": "invalid tool"})
            continue

        # Policy gate — the same RBAC/confirmation path a voice command hits.
        # An autonomous step that fails the gate is observed as denied; the
        # agent must adapt rather than bypass it.
        allowed = await policy_gate.check(tool=action, speaker_id=speaker_id or "agent",
                                          role=role or "user", session_id=session_id)
        if not allowed:
            transcript.append(f"OBSERVATION: action '{action}' was denied by policy "
                              f"(insufficient access). Do not retry it.")
            steps.append({"thought": thought, "action": action, "args": args,
                          "observation": "policy_denied"})
            continue

        try:
            result = await TOOL_REGISTRY[action](args, session_id)
        except Exception as e:
            logger.error(f"[{session_id[:8]}] react tool {action} failed: {e}")
            result = {"status": "error", "message": str(e)}

        tool_results.append({"tool": action, "result": result})
        # Feed a compact observation back into the loop — trim large payloads so
        # the reasoning context stays small.
        obs = result.get("spoken_reply") or result.get("message") or result.get("status") or ""
        obs = str(obs)[:300]
        transcript.append(f"STEP {step_i}: {thought}\nACTION: {action} {json.dumps(args)}\n"
                          f"OBSERVATION: {obs}")
        steps.append({"thought": thought, "action": action, "args": args, "observation": obs})

    # Hit the step cap (or an unparseable step) without an explicit final.
    return {"status": "incomplete", "final": "I worked on that but didn't fully complete it.",
            "steps": steps, "tool_results": tool_results}
