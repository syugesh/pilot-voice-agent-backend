"""
Tool registry — maps tool_name → async handler(args, session_id) → dict.
DS-A / FSE-A shared ownership.
"""
from tools.ppt_copilot import ppt_navigate, ppt_jump_to_title, ppt_summarize, ppt_delete_slide
from tools.flight_booking import flight_search, flight_book
from tools.tickets import ticket_create, ticket_update, ticket_close
from tools.knowledge import kb_search
from tools.crm import crm_lookup
from tools.general_qa import general_qa

TOOL_REGISTRY: dict = {
    "ppt_navigate":      ppt_navigate,
    "ppt_jump_to_title": ppt_jump_to_title,
    "ppt_summarize":     ppt_summarize,
    "ppt_delete_slide":  ppt_delete_slide,
    "ticket_create":     ticket_create,
    "ticket_update":     ticket_update,
    "ticket_close":      ticket_close,
    "kb_search":         kb_search,
    "crm_lookup":        crm_lookup,
    "flight_search":     flight_search,
    "flight_book":       flight_book,
    "general_qa":        general_qa,
}
