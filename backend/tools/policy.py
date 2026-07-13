"""
RBAC policy gate — checks speaker role before tool execution.
Destructive tools require identity-bound spoken confirmation.
DS-A / FSE-A shared.
"""
import asyncio, logging
from core.config import settings

logger = logging.getLogger("pilot.policy")

ROLE_PERMS = {
    "admin":     set(["*"]),
    "manager":   {"ppt_navigate","ppt_jump_to_title","ppt_summarize","ppt_edit_slide","ppt_generate_notes","ppt_last_action","ppt_add_slide","ppt_reorder_slide","ticket_create","ticket_update","ticket_close","kb_search","crm_lookup","travel_search","flight_book","general_qa","navigate_page","resolution_assess","escalate_ticket"},
    "csr":       {"ticket_create","ticket_update","ticket_close","kb_search","crm_lookup","travel_search","flight_book","general_qa","navigate_page","resolution_assess","escalate_ticket"},
    "operator":  {"ppt_navigate","ppt_jump_to_title","ppt_summarize","ppt_edit_slide","ppt_generate_notes","ppt_last_action","ppt_add_slide","ppt_reorder_slide","general_qa","navigate_page"},
    "developer": {"ppt_navigate","ppt_jump_to_title","ppt_summarize","ppt_edit_slide","ppt_generate_notes","ppt_last_action","ppt_add_slide","ppt_reorder_slide","kb_search","general_qa","navigate_page"},
    # "user" and "guest" = unrecognized/unenrolled speakers — allow basic navigation
    "user":      {"ppt_navigate","ppt_jump_to_title","ppt_summarize","ppt_edit_slide","ppt_generate_notes","ppt_last_action","ppt_add_slide","ppt_reorder_slide","kb_search","general_qa","travel_search","flight_book","navigate_page"},
    "guest":     {"ppt_navigate","ppt_jump_to_title","ppt_summarize","ppt_edit_slide","ppt_generate_notes","ppt_last_action","ppt_add_slide","ppt_reorder_slide","general_qa","travel_search","navigate_page"},
    "customer":  set(),
}


class PolicyGate:
    async def check(self, tool: str, speaker_id: str, role: str, session_id: str,
                    pre_confirmed: bool = False) -> bool:
        """RBAC + (for destructive tools) confirmation gate.

        `pre_confirmed=True` skips the *voice* confirmation latch — used only
        when the action arrives from a JWT-authenticated dashboard click, where
        the caller's identity is already proven by their bearer token (a
        stronger guarantee than a voice match). The RBAC role check still
        applies: a token whose role can't run the tool is still denied.
        """
        role = (role or "user").lower()
        perms = ROLE_PERMS.get(role, ROLE_PERMS["user"])
        allowed = "*" in perms or tool in perms

        if not allowed:
            logger.warning(f"[{session_id[:8]}] RBAC denied: tool={tool} speaker={speaker_id} role={role}")
            await self._emit_blocked(session_id, speaker_id, tool, "rbac_denied")
            await self._audit(session_id, speaker_id, role, tool, "blocked:rbac_denied")
            return False

        if tool in settings.DESTRUCTIVE_TOOLS and not pre_confirmed:
            confirmed, reason = await self._confirm(tool, speaker_id, session_id)
            if not confirmed:
                await self._audit(session_id, speaker_id, role, tool, f"blocked:{reason}")
                return False

        decision = "allowed:dashboard_confirmed" if (pre_confirmed and tool in settings.DESTRUCTIVE_TOOLS) else "allowed"
        await self._audit(session_id, speaker_id, role, tool, decision)
        return True

    async def _confirm(self, tool: str, speaker_id: str, session_id: str) -> tuple[bool, str]:
        """Block until the SAME speaker who triggered `tool` says an affirmative,
        or CONFIRM_TIMEOUT_S elapses. The next utterance in this session is
        checked against `state.pending_confirm` by front_llm._process() before
        normal LLM routing runs (see pipeline/front_llm.py) — that's what
        resolves the asyncio.Event this method waits on. A reply from any
        other speaker_id, or a role outside csr/manager/admin, does NOT
        resolve it — it's silently ignored so a customer's spoofed "yes
        confirm" can't satisfy someone else's pending confirmation."""
        import asyncio as _asyncio
        from queues.bus import bus
        from core.session_state import get_state
        from core.config import settings as _settings

        state = get_state(session_id)
        ev = _asyncio.Event()
        state.pending_confirm = {
            "tool": tool, "speaker_id": speaker_id, "event": ev, "resolved": False,
        }

        await bus.emit_event("confirm_prompt", {
            "tool": tool, "speaker": speaker_id,
            "message": f"Confirm {tool}? Say 'yes confirm' to proceed."
        }, session_id)

        try:
            await _asyncio.wait_for(ev.wait(), timeout=_settings.CONFIRM_TIMEOUT_S)
        except _asyncio.TimeoutError:
            await self._emit_blocked(session_id, speaker_id, tool, "confirm_timeout")
            state.pending_confirm = None
            return False, "confirm_timeout"

        # front_llm sets resolved=True only for a genuine same-speaker
        # affirmative; anything else (identity mismatch, explicit "no")
        # leaves resolved False even though the event was set to unblock us.
        confirmed = bool(state.pending_confirm and state.pending_confirm.get("resolved"))
        reason = "identity_mismatch" if not confirmed else "confirmed"
        if not confirmed:
            await self._emit_blocked(session_id, speaker_id, tool, "identity_mismatch")
        state.pending_confirm = None
        return confirmed, reason

    async def _emit_blocked(self, session_id, speaker_id, tool, reason):
        from queues.bus import bus
        await bus.emit_event("tool_blocked", {
            "tool": tool, "speaker": speaker_id, "reason": reason
        }, session_id)

    async def _audit(self, session_id, speaker_id, role, tool, decision):
        from db.engine import AsyncSessionLocal
        from db.models import AuditLog
        try:
            async with AsyncSessionLocal() as db:
                db.add(AuditLog(
                    session_id=session_id, speaker_id=speaker_id,
                    role=role, action="policy_check", tool=tool, decision=decision
                ))
                await db.commit()
        except Exception as e:
            logger.error(f"Audit write error: {e}")


policy_gate = PolicyGate()
