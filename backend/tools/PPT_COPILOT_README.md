# PPT Copilot

Voice- and click-driven PowerPoint editor. Upload a `.pptx`, or generate a new deck from a text description, then edit slides via a WYSIWYG canvas, voice commands, or the agent.

## Where the code lives

| Layer | File |
|---|---|
| API routes | `backend/api/ppt.py` (~90 routes: upload, generate, slide CRUD, geometry/style/text patches, history, notes) |
| Voice tool handlers | `backend/tools/ppt_copilot.py` — `ppt_navigate`, `ppt_jump_to_title`, `ppt_summarize`, `ppt_delete_slide`, `ppt_add_slide`, `ppt_reorder_slide`, `ppt_edit_slide`, `ppt_generate_notes`, `ppt_last_action` (registered in `backend/tools/registry.py`) |
| AI generation / notes | `backend/services/ppt_template_builder.py` — multi-provider LLM cascade (Gemini → Groq → local Ollama) for writing slide content and speaker notes |
| Frontend | `frontend/src/components/PPTView.tsx` — `PPTCopilotView` (the editor) + `PPTPageView` (session wiring: transcript filtering, live-transcript bar, mic toggle) |

`PPTView.tsx` is intentionally the single file for this feature's frontend — `PPTPageView` used to live separately in `transcript/PPTPageView.tsx` but was folded in so everything PPT-related is in one place.

## Core features

- **Upload** a real `.pptx` — converted via LibreOffice to per-slide PNG fidelity images plus an editable shape list (`GET/POST /api/v1/ppt/upload`).
- **Generate** a new deck from a text description (`/api/v1/ppt/generate`) — slide count and topic are user-specified, content is LLM-written.
- **WYSIWYG canvas** (`SlideCanvas` in `PPTView.tsx`) — drag/resize any text or image shape via 8 handles, double-click to edit text inline, a floating format toolbar (font, size, bold/italic/underline, color, alignment, layer order, delete) for the selected shape, and an "Add text box" button. Every edit PATCHes the backend immediately (no explicit save step) and the change is optimistic locally while the re-rendered thumbnail reconciles in the background (debounced ~700ms).
- **Kind-aware editing** — slides generated from the GD template (team, table, comparison, …) carry a declarative field schema (`KIND_FIELD_SCHEMA` in `ppt_template_builder.py`); the frontend renders any schema generically from ~6 field types (`KindFieldEditor`) instead of needing one hand-built form per slide kind.
- **Voice navigation** — "go to slide 3", "next slide", "delete this slide" etc. dispatch a `ppt_command` window event that `PPTCopilotView` listens for.
- **Delete slide is admin-only** — non-admin users get an "Access Denied" modal (`tool_blocked` event listener), enforced both client-side and via `backend/tools/policy.py`.
- **Per-user history** — previously uploaded/generated decks, MRU-sorted, reloadable via `/api/v1/ppt/history` and `/history/load/{sid}`.

## Known gaps

- No fullscreen "Presenter View" currently — it existed at one point during development but isn't in the active code as of the latest reference-alignment pass. Can be re-added if wanted.
- `ppt_qa`, `ppt_create_slides`, `ppt_clear_presentation`, `ppt_improvise_slide`, `ppt_save_slide` are commented out in the tool registry — planned/experimental, not wired up.
