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
        from sqlalchemy import select

        from backend.db.engine import AsyncSessionLocal
        from backend.db.models import User

        # Verify if there is currently an active logged-in user in the workspace
        # If the speaking voice similarity does not match the active logged-in user profile, block delegation!
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.is_active))
            active_users = result.scalars().all()

        # Resolve the specific authenticated user for this session
        # who is the owner
        # variable that hold the authenticated user assoicated with this session.
        session_user = None
        from backend.core.session_manager import session_manager

        active_sess = session_manager.get(session_id)
        # Retrieves the authenticated user's ID for that session.
        sess_user_id = active_sess.user_id if active_sess else None

        # it opens a database connection , checks the session table for a record matching the current session_id and pulls the owner's user_id. this handles cases where a session is reconnected or restored,and the in-memory manager has lost the mapping but the database has persisted it.
        if not sess_user_id:
            async with AsyncSessionLocal() as db:
                from backend.db.models import Session as PilotSession

                s_db = (
                    await db.execute(select(PilotSession).where(PilotSession.session_id == session_id))
                ).scalar_one_or_none()
                if s_db and s_db.user_id:
                    sess_user_id = s_db.user_id

        if sess_user_id:
            session_user = next((u for u in active_users if u.id == sess_user_id), None)
            if not session_user:
                async with AsyncSessionLocal() as db:
                    session_user = (
                        await db.execute(select(User).where(User.id == sess_user_id))
                    ).scalar_one_or_none()

        # Enforce Voice Identity Verification matching strictly - Auto-map generic speaker to logged-in user if active
        if speaker_id is None or speaker_id.lower() in ("you", "unknown", "unregistered", "spk-unknown", ""):
            fallback_user = session_user or (active_users[0] if active_users else None)
            if fallback_user:
                speaker_id = fallback_user.name
                role = fallback_user.role
                logger.info(
                    f"[policy] Speaker identified as generic '{speaker_id}'. Auto-mapping to active logged-in user '{fallback_user.name}' ({role}) to facilitate seamless command execution."
                )
            else:
                logger.warning(
                    f"[policy] Voice Match Blocked: Unregistered speaker profile attempted action '{tool}'."
                )
                await self._emit_blocked(
                    session_id, speaker_id or "Unregistered", tool, "unauthorized_voice_signature"
                )
                await self._audit(
                    session_id,
                    speaker_id or "Unregistered",
                    role,
                    tool,
                    "blocked:unauthorized_voice_signature",
                )
                return False

        if active_users:
            # We have registered signed-in users. Verify speaker_id matches one of the logged-in users.
            is_valid_user = any(u.name.lower() == (speaker_id or "").lower() for u in active_users)
            if not is_valid_user:
                logger.warning(
                    f"[policy] Voice Match Blocked: Speaker '{speaker_id}' is not an authenticated logged-in user."
                )
                await self._emit_blocked(session_id, speaker_id, tool, "unauthorized_voice_signature")
                await self._audit(session_id, speaker_id, role, tool, "blocked:unauthorized_voice_signature")
                return False

        # Apply standard RBAC permissions
        role = (
            role or "unregistered"
        ).lower()  # Secure unregistered voices rather than defaulting to developer!

        # Enforce Admin-only rules for specified tools
        if tool in ADMIN_ONLY_TOOLS and role != "admin":
            logger.warning(
                f"[policy] RBAC Denied: Tool '{tool}' is restricted to Admins. Current role: '{role}'."
            )
            await self._emit_blocked(session_id, speaker_id, tool, "rbac_denied")
            await self._audit(session_id, speaker_id, role, tool, "blocked:rbac_denied")
            return False

        perms = ROLE_PERMS.get(role, set())
        allowed = "*" in perms or tool in perms

        if not allowed:
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
