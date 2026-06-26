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
#from tools.system_tasks import database_query, write_file_tool, write_email_tool, send_email_tool, system_check, complex_calculation
#from tools.jira import jira_comment, jira_transition

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
    # "database_query":    database_query,
    # "write_file":        write_file_tool,
    # "write_email":       write_email_tool,
    # "send_email":        send_email_tool,
    # "system_check":      system_check,
    # "complex_calculation": complex_calculation,
    # "jira_comment":      jira_comment,
    # "jira_transition":   jira_transition,
}



# """
# Tool registry — maps tool_name → async handler(args, session_id) → dict.
# DS-A / FSE-A shared ownership.
# """
# from tools.ppt_copilot import ppt_navigate, ppt_jump_to_title, ppt_summarize, ppt_delete_slide
# from tools.flight_booking import flight_search, flight_book
# from tools.tickets import ticket_create, ticket_update, ticket_close
# from tools.knowledge import kb_search
# from tools.crm import crm_lookup
# from tools.general_qa import general_qa

# TOOL_REGISTRY: dict = {
#     "ppt_navigate":      ppt_navigate,
#     "ppt_jump_to_title": ppt_jump_to_title,
#     "ppt_summarize":     ppt_summarize,
#     "ppt_delete_slide":  ppt_delete_slide,
#     "ticket_create":     ticket_create,
#     "ticket_update":     ticket_update,
#     "ticket_close":      ticket_close,
#     "kb_search":         kb_search,
#     "crm_lookup":        crm_lookup,
#     "flight_search":     flight_search,
#     "flight_book":       flight_book,
#     "general_qa":        general_qa,
# }
