# """Single source of truth for in-memory slide/session state."""

# _slide_store: dict[str, list[dict]] = {}
# _current_slide: dict[str, int] = {}
# _latest_upload_sid: str = ""


# def set_latest_upload_sid(sid: str) -> None:
#     global _latest_upload_sid
#     _latest_upload_sid = sid


# def get_latest_upload_sid() -> str:
#     return _latest_upload_sid


# def resolve_sid(session_id: str) -> str:
#     """Use session_id if it has data, otherwise fall back to the last upload."""
#     return session_id if session_id in _slide_store else _latest_upload_sid


"""Single source of truth for in-memory slide/session state."""

_slide_store: dict[str, list[dict]] = {}
_current_slide: dict[str, int] = {}
_latest_upload_sid: str = ""


def set_latest_upload_sid(sid: str) -> None:
    global _latest_upload_sid
    _latest_upload_sid = sid


def get_latest_upload_sid() -> str:
    return _latest_upload_sid


def resolve_sid(session_id: str) -> str:
    """Use session_id if it has data, otherwise fall back to the last upload."""
    return session_id if session_id in _slide_store else _latest_upload_sid