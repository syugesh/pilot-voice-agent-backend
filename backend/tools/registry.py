"""
Tool registry — maps tool_name → async handler(args, session_id) → dict.
DS-A / FSE-A shared ownership.
"""

from backend.tools.flight_booking import flight_book, flight_search, flight_checkin

# from backend.tools.crm import crm_lookup
from backend.tools.general_qa import general_qa
from backend.tools.knowledge import kb_search
from backend.tools.meeting_summarizer import compile_meeting_minutes
# DISABLED: PILOT-native ppt tools removed when ppt_copilot.py was replaced
# by the upstream GD-template implementation, which doesn't define them.
# from backend.tools.ppt_copilot import (
#     ppt_clear_presentation,
#     ppt_create_slides,
#     ppt_improvise_slide,
#     ppt_qa,
#     ppt_save_slide,
#     route_page,
# )
from backend.tools.ppt_copilot import (
    ppt_add_slide,
    ppt_delete_slide,
    ppt_edit_slide,
    ppt_generate_notes,
    ppt_jump_to_title,
    ppt_last_action,
    ppt_navigate,
    ppt_reorder_slide,
    ppt_summarize,
)
from backend.tools.navigation import navigate_page
# Customer Resolution — CSR-dashboard resolution/escalation assessment,
# transferred from the upstream pilot-voice-agent-backend implementation.
from backend.tools.resolution_tool import resolution_assess, escalate_ticket
from backend.tools.tickets import ticket_create, ticket_update, ticket_close
from backend.tools.system_tasks import (
    cancel_task_tool,
    complex_calculation,
    database_query,
    send_email_tool,
    system_check,
    write_email_tool,
    write_file_tool,
)

TOOL_REGISTRY: dict = {
    "ppt_navigate": ppt_navigate,
    "ppt_jump_to_title": ppt_jump_to_title,
    "ppt_summarize": ppt_summarize,
    "ppt_delete_slide": ppt_delete_slide,
    # "ppt_qa": ppt_qa,
    # "ppt_create_slides": ppt_create_slides,
    "ppt_add_slide": ppt_add_slide,
    "ppt_reorder_slide": ppt_reorder_slide,
    "ppt_edit_slide": ppt_edit_slide,
    "ppt_generate_notes": ppt_generate_notes,
    "ppt_last_action": ppt_last_action,
    # "ppt_clear_presentation": ppt_clear_presentation,
    # "ppt_improvise_slide": ppt_improvise_slide,
    # "ppt_save_slide": ppt_save_slide,
    # "route_page": route_page,
    "navigate_page": navigate_page,
    # Customer Resolution
    "resolution_assess": resolution_assess,
    "escalate_ticket": escalate_ticket,
    "ticket_create": ticket_create,
    "ticket_update": ticket_update,
    "ticket_close": ticket_close,
    "kb_search": kb_search,
    # "crm_lookup":        crm_lookup,
    "flight_search": flight_search,
    "flight_book": flight_book,
    "flight_checkin": flight_checkin,
    "general_qa": general_qa,
    "database_query": database_query,
    "write_file": write_file_tool,
    "write_email": write_email_tool,
    "send_email": send_email_tool,
    "system_check": system_check,
    "complex_calculation": complex_calculation,
    "cancel_task": cancel_task_tool,
    "compile_minutes": compile_meeting_minutes,
}
