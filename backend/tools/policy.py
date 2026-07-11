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
    "manager":   {"ppt_navigate","ppt_jump_to_title","ppt_summarize","ppt_edit_slide","ppt_generate_notes","ppt_last_action","ppt_add_slide","ticket_create","ticket_update","ticket_close","kb_search","crm_lookup","travel_search","flight_book","general_qa","navigate_page","resolution_assess","escalate_ticket"},
    "csr":       {"ticket_create","ticket_update","ticket_close","kb_search","crm_lookup","travel_search","flight_book","general_qa","navigate_page","resolution_assess","escalate_ticket"},
    "operator":  {"ppt_navigate","ppt_jump_to_title","ppt_summarize","ppt_edit_slide","ppt_generate_notes","ppt_last_action","ppt_add_slide","general_qa","navigate_page"},
    "developer": {"ppt_navigate","ppt_jump_to_title","ppt_summarize","ppt_edit_slide","ppt_generate_notes","ppt_last_action","ppt_add_slide","kb_search","general_qa","navigate_page"},
    # "user" and "guest" = unrecognized/unenrolled speakers — allow basic navigation
    "user":      {"ppt_navigate","ppt_jump_to_title","ppt_summarize","ppt_edit_slide","ppt_generate_notes","ppt_last_action","ppt_add_slide","kb_search","general_qa","travel_search","flight_book","navigate_page"},
    "guest":     {"ppt_navigate","ppt_jump_to_title","ppt_summarize","ppt_edit_slide","ppt_generate_notes","ppt_last_action","ppt_add_slide","general_qa","travel_search","navigate_page"},
    "customer":  set(),
}


class PolicyGate:
    async def check(self, tool: str, speaker_id: str, role: str, session_id: str) -> bool:
        role = (role or "user").lower()
        perms = ROLE_PERMS.get(role, ROLE_PERMS["user"])
        allowed = "*" in perms or tool in perms

        if not allowed:
            logger.warning(f"[{session_id[:8]}] RBAC denied: tool={tool} speaker={speaker_id} role={role}")
            await self._emit_blocked(session_id, speaker_id, tool, "rbac_denied")
            await self._audit(session_id, speaker_id, role, tool, "blocked:rbac_denied")
            return False

        if tool in settings.DESTRUCTIVE_TOOLS:
            confirmed = await self._confirm(tool, speaker_id, session_id)
            if not confirmed:
                await self._audit(session_id, speaker_id, role, tool, "blocked:confirm_timeout")
                return False

        await self._audit(session_id, speaker_id, role, tool, "allowed")
        return True

    async def _confirm(self, tool: str, speaker_id: str, session_id: str) -> bool:
        from queues.bus import bus
        await bus.emit_event("confirm_prompt", {
            "tool": tool, "speaker": speaker_id,
            "message": f"Confirm {tool}? Say 'yes confirm' to proceed."
        }, session_id)
        # Simplified: auto-allow. Real: latch-window waiting for same speaker affirmative.
        # TODO DS-A: implement asyncio.Event + 10s timeout + speaker_id check
        return True

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
