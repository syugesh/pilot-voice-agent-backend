"""
Tool registry — maps tool_name → async handler(args, session_id) → dict.
DS-A / FSE-A shared ownership.
"""
from tools.ppt_copilot import ppt_navigate, ppt_jump_to_title, ppt_summarize, ppt_delete_slide, ppt_edit_slide, ppt_generate_notes, ppt_last_action, ppt_add_slide
from tools.travel_planner import travel_search, flight_book
from tools.tickets import ticket_create, ticket_update, ticket_close
from tools.knowledge import kb_search
from tools.crm import crm_lookup
from tools.general_qa import general_qa
from tools.navigation import navigate_page

TOOL_REGISTRY: dict = {
    "ppt_navigate":      ppt_navigate,
    "ppt_jump_to_title": ppt_jump_to_title,
    "ppt_summarize":     ppt_summarize,
    "ppt_delete_slide":  ppt_delete_slide,
    "ppt_edit_slide":    ppt_edit_slide,
    "ppt_generate_notes": ppt_generate_notes,
    "ppt_last_action":   ppt_last_action,
    "ppt_add_slide":     ppt_add_slide,
    "ticket_create":     ticket_create,
    "ticket_update":     ticket_update,
    "ticket_close":      ticket_close,
    "kb_search":         kb_search,
    "crm_lookup":        crm_lookup,
    "travel_search":     travel_search,
    "flight_book":       flight_book,
    "general_qa":        general_qa,
    "navigate_page":     navigate_page,
}
