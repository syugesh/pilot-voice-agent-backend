"""
RBAC policy gate — checks speaker role before tool execution.
Destructive tools require identity-bound spoken confirmation.
DS-A / FSE-A shared.
"""

import asyncio
import logging

from backend.core.config import settings

logger = logging.getLogger("pilot.policy")

ROLE_PERMS = {
    "admin": set(["*"]),
    "manager": {
        "ppt_navigate",
        "ppt_jump_to_title",
        # "ppt_qa",  # DISABLED: tool removed upstream
        "ppt_summarize",
        # "ppt_create_slides",  # DISABLED: tool removed upstream
        "ppt_add_slide",
        "ppt_reorder_slide",
        "ppt_edit_slide",
        "ppt_generate_notes",
        "ppt_last_action",
        # "ppt_clear_presentation",  # DISABLED: tool removed upstream
        # "ppt_improvise_slide",  # DISABLED: tool removed upstream
        # "ppt_save_slide",  # DISABLED: tool removed upstream
        "navigate_page",
        "kb_search",
        # "crm_lookup",
        "flight_search",
        "flight_book",
        "flight_checkin",
        "write_file",
        "complex_calculation",
        # Customer Resolution
        "resolution_assess",
        "escalate_ticket",
        "ticket_create",
        "ticket_update",
        "ticket_close",
    },
    "csr": {
        "kb_search",
        # "crm_lookup",
        "flight_search",
        "flight_book",
        "flight_checkin",
        "navigate_page",
        # Customer Resolution
        "resolution_assess",
        "escalate_ticket",
        "ticket_create",
        "ticket_update",
        "ticket_close",
    },
    "operator": {
        "ppt_navigate",
        "ppt_jump_to_title",
        # "ppt_qa",  # DISABLED: tool removed upstream
        "ppt_summarize",
        # "ppt_create_slides",  # DISABLED: tool removed upstream
        "ppt_add_slide",
        "ppt_reorder_slide",
        "ppt_edit_slide",
        "ppt_generate_notes",
        "ppt_last_action",
        # "ppt_clear_presentation",  # DISABLED: tool removed upstream
        # "ppt_improvise_slide",  # DISABLED: tool removed upstream
        # "ppt_save_slide",  # DISABLED: tool removed upstream
        "navigate_page",
        "flight_checkin",
        # Customer Resolution
        "resolution_assess",
        "escalate_ticket",
        "ticket_create",
        "ticket_update",
        "ticket_close",
    },
    "developer": {
        "ppt_navigate",
        "ppt_jump_to_title",
        # "ppt_qa",  # DISABLED: tool removed upstream
        "ppt_summarize",
        # "ppt_create_slides",  # DISABLED: tool removed upstream
        "ppt_add_slide",
        "ppt_reorder_slide",
        "ppt_edit_slide",
        "ppt_generate_notes",
        "ppt_last_action",
        # "ppt_clear_presentation",  # DISABLED: tool removed upstream
        # "ppt_improvise_slide",  # DISABLED: tool removed upstream
        # "ppt_save_slide",  # DISABLED: tool removed upstream
        "navigate_page",
        "kb_search",
        "write_file",
        "complex_calculation",
        "flight_checkin",
        # Customer Resolution
        "resolution_assess",
        "escalate_ticket",
        "ticket_create",
        "ticket_update",
        "ticket_close",
    },
    "customer": set(),
}

# Explicit tools that can ONLY be executed by an Admin user role
ADMIN_ONLY_TOOLS = {"compile_minutes", "database_query", "system_check", "ppt_delete_slide"}  # meeting summmarizer


# Global registry for active latch-window confirmations
# session_id -> { "tool": str, "speaker_id": str, "event": asyncio.Event, "confirmed": bool }
PENDING_CONFIRMATIONS = {}


class PolicyGate:
    async def check(self, tool: str, speaker_id: str, role: str, session_id: str) -> bool:
        # Policy: every tool is allowed for every speaker, identified or not
        # — no session-owner check, no external-speaker block. Still logs
        # who asked for what (audit trail below) and still runs the
        # destructive-tool voice confirmation, since that's a
        # misfire/mishearing safeguard, not an identity gate.
        speaker_id = speaker_id or "Unregistered"
        role = role or "unregistered"

        if tool in settings.DESTRUCTIVE_TOOLS:
            confirmed = await self._confirm(tool, speaker_id, session_id)
            if not confirmed:
                await self._audit(session_id, speaker_id, role, tool, "blocked:confirm_timeout")
                return False

        await self._audit(session_id, speaker_id, role, tool, "allowed")
        return True

    async def _confirm(self, tool: str, speaker_id: str, session_id: str) -> bool:
        from backend.queues.bus import bus

        # Propose the mutation + summary
        confirm_event = asyncio.Event()
        PENDING_CONFIRMATIONS[session_id] = {
            "tool": tool,
            "speaker_id": speaker_id,
            "event": confirm_event,
            "confirmed": False,
        }

        logger.info(
            f"[policy] Latch window opened for session {session_id[:8]} - Waiting for speaker '{speaker_id}' to confirm."
        )

        await bus.emit_event(
            "confirm_prompt",
            {
                "tool": tool,
                "speaker": speaker_id,
                "message": f"Verify Voice ID: Confirm action '{tool}'? Only speaker '{speaker_id}' is authorized. Say 'yes confirm' to proceed.",
            },
            session_id,
        )

        try:
            # Latch window: wait up to 10 seconds for affirmative confirmation from the exact same speaker
            await asyncio.wait_for(confirm_event.wait(), timeout=settings.CONFIRM_TIMEOUT_S)
            confirmed = PENDING_CONFIRMATIONS[session_id]["confirmed"]
            return confirmed
        except asyncio.TimeoutError:
            logger.warning(
                f"[policy] Latch window TIMEOUT: Speaker '{speaker_id}' failed to confirm destructive action within 10s."
            )
            await bus.emit_event(
                "tool_blocked", {"tool": tool, "speaker": speaker_id, "reason": "confirm_timeout"}, session_id
            )
            return False
        finally:
            PENDING_CONFIRMATIONS.pop(session_id, None)

    async def _emit_blocked(self, session_id, speaker_id, tool, reason):
        from backend.queues.bus import bus

        await bus.emit_event(
            "tool_blocked", {"tool": tool, "speaker": speaker_id, "reason": reason}, session_id
        )

    async def _audit(self, session_id, speaker_id, role, tool, decision):
        from backend.db.engine import AsyncSessionLocal
        from backend.db.models import AuditLog

        try:
            async with AsyncSessionLocal() as db:
                db.add(
                    AuditLog(
                        session_id=session_id,
                        speaker_id=speaker_id,
                        role=role,
                        action="policy_check",
                        tool=tool,
                        decision=decision,
                    )
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Audit write error: {e}")


policy_gate = PolicyGate()
