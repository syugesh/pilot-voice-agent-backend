# PILOT

**Portable Intelligent Listener for Open Tasking**

One real-time voice pipeline — shared speaker identification, routing, and policy enforcement — reused across three copilots: a voice-driven presentation builder, a silent customer-support call analyst, and a general assistant with travel booking. This repo is the backend (FastAPI + WebSockets); the companion frontend lives in `pilot-voice-agent-frontend`.

> If you only read one section, read [§3 Usecases](#3-the-three-usecases) and [§5 End-to-end event flow](#5-end-to-end-event-flow-one-utterance-start-to-finish).

---

## Table of contents

1. [What this is](#1-what-this-is)
2. [System architecture](#2-system-architecture)
3. [The three usecases](#3-the-three-usecases)
4. [The audio pipeline in detail](#4-the-audio-pipeline-in-detail)
5. [End-to-end event flow](#5-end-to-end-event-flow-one-utterance-start-to-finish)
6. [Identity & speaker recognition](#6-identity--speaker-recognition)
7. [Policy gate & RBAC](#7-policy-gate--rbac)
8. [Background agent & silent observers](#8-background-agent--silent-observers)
9. [Tool catalog](#9-tool-catalog)
10. [PPT Copilot internals](#10-ppt-copilot-internals)
11. [Data model](#11-data-model)
12. [WebSocket contract](#12-websocket-contract)
13. [Repo layout](#13-repo-layout)
14. [Configuration](#14-configuration)
15. [Running it locally](#15-running-it-locally)
16. [Known limitations](#16-known-limitations)

---

## 1. What this is

Most voice-agent projects are one microphone feeding one LLM call: transcribe, prompt, speak, repeat. PILOT is built around a different premise — that a real deployment needs **one shared runtime** (session state machine, speaker identity, policy enforcement, audit log) that gets reused across different fronts, rather than a new stack per product.

A session picks a `usecase` (`ppt` / `customercare` / `general`) and the same pipeline underneath changes *what it's for*, not *how it works*.

---

## 2. System architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                   BROWSER                                    │
│                                                                                │
│   ┌────────────┐   ┌──────────────┐   ┌───────────────┐   ┌───────────────┐   │
│   │ AudioCapture│   │ PilotWSClient│   │ SessionStore  │   │  Dashboard UI │   │
│   │ mic → PCM16 │──▶│ 2× WebSocket │◀─▶│  (Zustand)    │◀─▶│ PPT / Care /  │   │
│   │  16kHz      │   │ audio+events │   │               │   │ General views │   │
│   └────────────┘   └──────┬───────┘   └───────────────┘   └───────────────┘   │
└─────────────────────────────┼─────────────────────────────────────────────────┘
                              │  wss://…/ws/audio (binary PCM)
                              │  wss://…/ws/events (JSON, bidirectional push)
┌─────────────────────────────┼─────────────────────────────────────────────────┐
│                       FASTAPI BACKEND       │                                 │
│                                              ▼                                │
│   ┌────────────────────────────────────────────────────────────────────────┐  │
│   │                         SESSION MANAGER (state machine)                │  │
│   │      IDLE → LISTENING → PROCESSING → DELEGATING/SPEAKING → … → ENDED   │  │
│   └───────────────┬───────────────────────────────────┬────────────────────┘  │
│                   ▼                                   ▼                       │
│   ┌───────────────────────────────┐   ┌───────────────────────────────────┐   │
│   │        AUDIO PIPELINE         │   │         BACKGROUND SUPERVISOR     │   │
│   │  VAD → Diarizer → ASR →       │   │  job queue · tool_start/tool_end  │   │
│   │  SmartTurn → wake-word gate   │   │  spoken-reply synthesis · barge-in│   │
│   └───────────────┬───────────────┘   └───────────────┬───────────────────┘   │
│                   ▼                                   │                       │
│   ┌───────────────────────────────┐                   │                       │
│   │         FRONT LLM             │                   │                       │
│   │  intent → {action,tool,args}  │───────────────────▶│                       │
│   │  regex fast-paths + Ollama    │                   │                       │
│   └───────────────┬───────────────┘                   │                       │
│                   ▼                                   ▼                       │
│   ┌────────────────────────────────────────────────────────────────────────┐  │
│   │                           POLICY GATE (RBAC)                          │  │
│   │     usecase allowlist  →  role permission  →  identity-bound confirm  │  │
│   └───────────────┬────────────────────────────────────────────────────────┘  │
│                   ▼                                                          │
│   ┌────────────────────────────────────────────────────────────────────────┐  │
│   │                            TOOL REGISTRY                               │  │
│   │  crm · tickets · kb_search · navigation · travel_planner · ppt_copilot │  │
│   │  resolution_tool · general_qa · react_agent (ReAct loop)               │  │
│   └───────────────┬────────────────────────────────────────────────────────┘  │
│                   ▼                                                          │
│   ┌───────────────────────────────┐   ┌───────────────────────────────────┐   │
│   │      GATEWAY EMIT (fork)      │   │         SILENT OBSERVERS          │   │
│   │  speak (TTS) ── or ──         │   │  care_observer · sentiment ·      │   │
│   │  write to dashboard silently  │   │  resolution engine                │   │
│   └───────────────┬───────────────┘   └───────────────┬───────────────────┘   │
│                   ▼                                   ▼                       │
│              /ws/events  ◀──────────────────────────────                      │
└─────────────────────────────┼─────────────────────────────────────────────────┘
                              ▼
                    ┌───────────────────┐
                    │  SQLite (pilot.db)│
                    │  Users · Sessions  │
                    │  Transcripts ·     │
                    │  Tickets · KB ·    │
                    │  VoiceEnrollments ·│
                    │  AuditLog          │
                    └───────────────────┘

  External / local model providers (cascade: cloud → cloud → local → template):
  Whisper (mlx / faster-whisper) · WeSpeaker ECAPA-TDNN · Ollama (qwen3) ·
  Gemini · Groq · edge-tts → Kokoro-ONNX → OS voice · Tavily · SerpApi ·
  flight-mcp (Node MCP server, stdio)
```

---

## 3. The three usecases

```
┌─────────────────────┐   ┌──────────────────────────┐   ┌─────────────────────┐
│     PPT COPILOT      │   │     CUSTOMER CARE         │   │  GENERAL ASSISTANT  │
│                       │   │                            │   │                       │
│ "Build me a 6-slide   │   │ Rep + customer share one   │   │ Open Q&A, navigation, │
│  pitch on X"          │   │ call line. PILOT never     │   │ and a travel-booking  │
│                       │   │ speaks — it only writes    │   │ agent (flights,       │
│ Clones real slides    │   │ to the rep's screen.       │   │ hotels, trains, cabs) │
│ out of the actual     │   │                            │   │                       │
│ PowerPoint template — │   │ Tracks sentiment/          │   │ Resolves city names   │
│ not auto-drawn boxes  │   │ frustration turn by turn,  │   │ to routes, checks live │
│                       │   │ pulls the right KB article,│   │ pricing via MCP/Tavily,│
│ Voice: navigate /     │   │ recommends resolve vs.     │   │ books behind a spoken  │
│ edit / reorder /      │   │ escalate with reasoning    │   │ confirmation gate      │
│ delete slides, add    │   │ attached                    │   │                       │
│ speaker notes         │   │                            │   │                       │
└─────────────────────┘   └──────────────────────────┘   └─────────────────────┘
        speaks                    silent (dashboard only)         speaks
```

---

## 4. The audio pipeline in detail

```
 mic (browser)
   │ Float32 → Int16 PCM, 16kHz mono, echo-cancelled
   ▼
 /ws/audio  (binary frames)
   │
   ▼
┌─────────────┐     ┌───────────────┐     ┌──────────────────┐
│     VAD     │────▶│   Diarizer     │────▶│  Identity Resolver│
│ speech / no  │     │ WeSpeaker      │     │ cosine + margin   │
│ speech frames│     │ ECAPA-TDNN     │     │ vs. enrolled      │
└─────────────┘     │ embeddings,    │     │ voiceprints;      │
                     │ majority vote  │     │ falls back to     │
                     │ per turn       │     │ JWT session owner │
                     └───────────────┘     │ on borderline match│
                                            └─────────┬─────────┘
                                                       ▼
                                            ┌───────────────────┐
                                            │        ASR         │
                                            │ Whisper distil-     │
                                            │ large-v3 (mlx on    │
                                            │ Apple Silicon /     │
                                            │ faster-whisper CPU) │
                                            │ + hallucination     │
                                            │ filter               │
                                            └─────────┬───────────┘
                                                       ▼
                                            ┌───────────────────┐
                                            │   asr_worker        │
                                            │ buffers partial      │
                                            │ turns (≤3 segs /     │
                                            │ 200 chars), 2.5s      │
                                            │ silence force-flush   │
                                            └─────────┬───────────┘
                                                       ▼
                                            ┌───────────────────┐
                                            │   Smart Turn         │
                                            │ (linguistic dangling- │
                                            │ word/punctuation      │
                                            │ heuristic — see §16)  │
                                            └─────────┬───────────┘
                                                       ▼
                                            ┌───────────────────┐
                                            │   Wake-word gate      │
                                            │ "hey pilot" / "ok      │
                                            │ pilot" — checks first  │
                                            │ 4 words only           │
                                            └─────────┬───────────┘
                                                       ▼
                                              → Front LLM (§5)
```

**LLM serving priority.** A single local Ollama instance serves both instant turn-routing and slow background jobs (deck generation, ReAct steps). `core/llm_gate.py` runs a two-lane queue — `high` priority (routing/classification) always preempts `low` priority (generation) — so a 6-second deck-writing job never adds 6 seconds of latency to the next sentence someone speaks.

**Barge-in.** A stop-phrase or new utterance short-circuits before any LLM call, cancels in-flight TTS and background jobs via `core/cancel_tokens.py`, and pushes a `tts_stop` event so playback halts immediately client-side.

---

## 5. End-to-end event flow (one utterance, start to finish)

```
 USER SPEAKS
     │
     ▼
 ① frontend: AudioCapture streams PCM over /ws/audio
     │
     ▼
 ② backend: VAD → Diarizer → Identity Resolver → ASR → asr_worker buffer
     │                                                     │
     │                                       emits `transcript` on /ws/events
     ▼
 ③ Smart Turn says "utterance complete"
     │
     ▼
 ④ Front LLM classifies: regex fast-path, or Ollama → {action, tool, args, mode}
     │                                          emits `route_decision`
     ▼
 ⑤ Policy Gate: usecase allowlist → role permission → destructive-action confirm?
     │                                          emits `confirm_prompt` if needed,
     │                                          emits `tool_blocked` if denied
     ▼
 ⑥ Background Supervisor enqueues the job
     │                                          emits `job_queued`, `tool_start`
     ▼
 ⑦ Tool executes (crm / tickets / kb_search / travel_planner / ppt_copilot / …)
     │            (react_agent may run several ⑤→⑦ loops internally, ≤5 steps)
     │                                          emits `tool_end`
     ▼
 ⑧ Result → bg_agent turns JSON into a natural sentence (Gemini→Groq→Ollama→template)
     │
     ▼
 ⑨ gateway_emit forks on usecase:
     │
     ├── ppt / general  →  TTS synthesis (edge-tts→Kokoro→OS voice)
     │                     emits `tts_audio` chunks over /ws/events
     │                     frontend plays via Web Audio API
     │
     └── customercare   →  written silently to the dashboard
                           emits `care_observe` / `resolution_update` / `agent_note`
                           — never spoken, rep reads it on screen
     │
     ▼
 ⑩ AuditLog row written for every policy check + tool call (session_id, actor, decision)
     │
     ▼
 SESSION STATE returns to LISTENING (or ENDED)
```

In parallel, independent of any single utterance:

```
 EVERY 1.2s (debounced)                    EVERY CUSTOMER TURN
     │                                             │
     ▼                                             ▼
 care_observer: symptom-cue timeline      sentiment.py: RoBERTa sentiment +
 + LLM issue synopsis → `care_observe`    urgency/repetition/caps → blended
                                          frustration score → `sentiment_update`

 EVERY 4 NEW TURNS
     │
     ▼
 care_observer spawns a scoped ReAct step (kb_search / crm_lookup /
 resolution_assess) → conclusion routed silently → `agent_note`
```

---

## 6. Identity & speaker recognition

This is **voice biometrics**, not customer/patient enrollment:

1. A user enrolls their own voice through the dashboard (`api/enrollment.py`) — audio is decoded to 16kHz PCM via ffmpeg, embedded with WeSpeaker, stored as a BLOB on `VoiceEnrollment` keyed to their `User`.
2. At runtime, every turn is embedded and compared by cosine similarity against all enrolled voiceprints (threshold `0.6`, margin `0.05` to avoid confusing two enrolled speakers with each other).
3. Unenrolled voices resolve to `"Customer"` / `"Unknown Speaker"` rather than being guessed into an identity — deliberately, since mislabeling an unenrolled customer as the session owner would break both the transcript and RBAC.
4. Borderline matches fall back to the JWT-authenticated session owner only when plausible, never for a clearly different voice.

---

## 7. Policy gate & RBAC

Enforced **twice**, by design (defense in depth):

```
              ┌───────────────────────┐
 Front LLM ──▶│ usecase tool allowlist │  (per usecase: ppt/customercare/general
              │  (classification time) │   only exposes its own relevant tools)
              └───────────┬───────────┘
                          ▼
              ┌───────────────────────┐
              │   PolicyGate (dispatch)│
              │  role → permission map │  admin / manager / csr / operator /
              │  (ROLE_PERMS)           │  developer / user / guest / customer
              └───────────┬───────────┘
                          ▼
              ┌───────────────────────┐
              │ destructive-action     │  flight_book, ticket_close require:
              │ confirmation           │  live spoken "yes, confirm" from the
              │                        │  SAME identified voice, within timeout
              │                        │  (or pre-confirmed via dashboard click)
              └───────────┬───────────┘
                          ▼
                    AuditLog row
```

The autonomous ReAct agent (`services/react_agent.py`) is not exempt — every tool call inside its reasoning loop passes through the same gate, so the agent cannot act past permissions no matter how confident its own reasoning is.

---

## 8. Background agent & silent observers

| Component | Role |
|---|---|
| `core/bg_supervisor.py` | Single-consumer job queue; runs tool calls out-of-band, emits `tool_start`/`tool_end`, writes `AuditLog`, hands results to `bg_agent` for spoken synthesis. Supports "interrupt" jobs for barge-in. |
| `services/bg_agent.py` | Turns raw tool JSON into a natural spoken sentence — cascades Gemini → Groq → local Ollama → hardcoded template. |
| `services/react_agent.py` | Reason→act→observe loop, capped at 5 steps, for free-form goals the Front LLM hands off. Every step still passes `PolicyGate`. |
| `services/care_observer.py` | Silent customer-care analyst. Debounced (1.2s) symptom-timeline + LLM synopsis; every 4 turns spawns a scoped ReAct step feeding the CSR's on-screen recommendation. Never speaks. |
| `services/sentiment.py` | Per-customer-turn frustration/urgency scoring (`cardiffnlp/twitter-roberta-base-sentiment-latest` blended with linguistic signals). |
| `services/resolution.py` | Deterministic rule layer (outage duration, repeat contacts, sustained frustration, weak KB match) sets a **confidence ceiling** the LLM reasoning pass cannot exceed — the model can't talk itself past a genuine escalation floor. |

---

## 9. Tool catalog

| Tool | What it does |
|---|---|
| `crm` | Customer lookup (mock data — see [§16](#16-known-limitations)) |
| `general_qa` | Open Q&A grounded via Tavily web search; cascades Ollama → Gemini → Groq |
| `knowledge` (`kb_search`) | FAISS + `all-MiniLM-L6-v2` semantic RAG over a seeded ISP/telecom troubleshooting KB |
| `navigation` | Voice-driven SPA page switching — emits a WS event, no DB writes |
| `policy` | The `PolicyGate` itself — RBAC + confirmation, described in [§7](#7-policy-gate--rbac) |
| `ppt_copilot` | Full PPT voice surface — navigate/edit/delete/reorder/generate-notes/add-slide (see [§10](#10-ppt-copilot-internals)) |
| `resolution_tool` | `resolution_assess` (CSR dashboard orchestrator) + `escalate_ticket` |
| `tickets` | CRUD for support tickets against the `Ticket` table |
| `travel_planner` | Flights/hotels/trains/cabs — resolves city→IATA, calls `flight-mcp` (Node MCP server over stdio) for live pricing, rich mock fallback otherwise |
| `react_agent` | Multi-step autonomous reasoning loop over the above tools |

---

## 10. PPT Copilot internals

```
 "Build me a pitch on X"
          │
          ▼
 LLM generates kind-tagged JSON content
 (cover / agenda / text / two_column / comparison / team /
  speaker_1 / speaker_4 / key_message / table / chapter / thank_you)
          │
          ▼
 each "kind" maps to candidate slide indices in the REAL template
 (Grid Dynamics template + Minimalist variant — Google Slides export)
          │
          ▼
 low-level XML manipulation clones that exact slide (branding, background
 preserved) and overwrites placeholder text via known shape-id "slot maps"
          │
          ▼
 thumbnail regeneration: LibreOffice → pdftoppm
          │
          ▼
 voice edits after generation: add / edit / delete / reorder slides,
 auto-position picker (LLM chooses best insertion point)
```

Built this way — cloning real branded slides rather than drawing shapes — because `python-pptx` has no clean generative layout API and the source template is a bespoke Google Slides export.

---

## 11. Data model

```
 User ───────< VoiceEnrollment          (embedding BLOB per user)
   │
   ├──────< Session                     (usecase, state, ring-buffer snapshot)
   │             │
   │             ├──────< TranscriptLog  (every utterance)
   │             ├──────< AuditLog       (every policy check + tool call)
   │             └──────< PPTSlideVersion (edit history)
   │
   └──────< Ticket                      (support tickets, escalation fields)

 KBDocument   (RAG corpus — independent of Session)
```

---

## 12. WebSocket contract

Two sockets per session: `/ws/audio` (binary PCM in) and `/ws/events` (JSON push, bidirectional). The event schema is intentionally mirrored by hand between `contracts/ws_events.py` and `contracts/ws_events.ts` rather than codegen'd.

| Event | Payload | Fired by |
|---|---|---|
| `transcript` | transcribed turn | ASR pipeline |
| `route_decision` | `{action, tool, speaker}` | Front LLM |
| `job_queued` / `tool_start` / `tool_end` | job/tool lifecycle | Background supervisor |
| `confirm_prompt` | `{tool, speaker, message}` | Policy gate |
| `tool_blocked` | `{tool, speaker, reason}` | Policy gate |
| `tts_audio` / `tts_stop` | audio chunks / stop signal | TTS + barge-in |
| `ppt_command` / `navigate_page` | `{action, index?}` / `{page}` | PPT copilot / navigation tool |
| `session_state` | `{state}` | Session manager |
| `sentiment_update` | sentiment/frustration/urgency | `services/sentiment.py` |
| `resolution_update` | recommendation + reasoning + KB refs | `resolution_tool` |
| `care_observe` | synopsis + symptom timeline | `care_observer` (customercare only) |
| `agent_note` | ReAct conclusion | `care_observer` → CSR dashboard (silent) |

A server-side 20-event backlog buffer (`_session_backlog`) replays missed events across a WebSocket reconnect.

---

## 13. Repo layout

```
backend/
├── api/            HTTP + WS route handlers (auth, sessions, ws_audio, ws_events, …)
├── core/           session state machine, LLM gate, cancel tokens, config, security
├── pipeline/       diarizer, ASR worker, front_llm routing, identity resolver, VAD
├── services/       stt, tts, sentiment, care_observer, react_agent, ppt builder, …
├── tools/          the tool registry described in §9
├── db/             SQLAlchemy models + engine
├── queues/         async job bus
├── flight-mcp/     Node MCP server for live flight/travel pricing (stdio)
├── data/           sqlite DB, ppt templates, kokoro TTS assets
└── tests/
contracts/
└── ws_events.py    WS event schema (Python side — see contracts/ws_events.ts in frontend repo)
```

---

## 14. Configuration

Provider choice per capability is entirely env-driven (`backend/.env`, template in `.env.example`):

```
ASR_PROVIDER=whisper          TTS_PROVIDER=edge_tts
EMBED_PROVIDER=wespeaker      FRONT_LLM_PROVIDER=ollama
BG_LLM_PROVIDER=gemini        OLLAMA_MODEL=qwen3:8b
```

Every LLM-touching service follows the same fallback cascade — cloud provider → next cloud provider → local Ollama → deterministic template — so missing an API key degrades capability rather than crashing a session.

---

## 15. Running it locally

```bash
cd backend
uv sync              # or: pip install -r requirements.txt
cp .env.example .env # fill in provider keys as available; local-only works with none
uvicorn main:app --reload

cd ../flight-mcp
npm install          # only needed for live travel pricing

cd ../../pilot-voice-agent-frontend/frontend
npm install
npm run dev
```

---

## 16. Known limitations

- **Turn-end detection** is currently a linguistic heuristic (dangling-word/punctuation classifier), not the ML "smart-turn" model the naming implies — the real model couldn't be loaded via standard tooling at time of writing.
- **CRM data and some travel results** are static mocks used when a live provider isn't configured/reachable — intentional for demos, not yet wired to a real system of record.

Both are isolated behind clean interfaces (`services/smart_turn.py`, `tools/crm.py`, `tools/travel_planner.py` fallback paths) specifically so they can be swapped for production implementations without touching anything upstream.
