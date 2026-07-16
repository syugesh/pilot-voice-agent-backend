# Voice Enrollment & Speaker Diarization

Identifies *who* is speaking during a live PILOT session or Talkinia meeting — two separate jobs that work together: telling different voices apart in real time (diarization), and matching a voice against a database of enrolled people (identification).

## Where the code lives

| Job | File |
|---|---|
| Enrollment API | `backend/api/enrollment.py` — start/submit-audio/finalize/list/delete a voice profile |
| Enrollment + identification | `backend/services/enrollment.py` — `EmbedProvider` (voice → embedding) and `identify_speaker()` (embedding → DB match) |
| In-session diarization | `backend/services/diarizer.py` — `PyannoteProvider`, real-time clustering of distinct voices within one session |
| Pipeline wiring | `backend/pipeline/diarizer.py` (assigns raw `spk-N` cluster labels) → `backend/pipeline/identity_resolver.py` (resolves `spk-N` → a real name, or an honest "unmatched" label) |
| Frontend capture UI | `frontend/src/components/EnrollmentCapture.tsx` |

## How it works

1. **Diarization** (`PyannoteProvider`): every audio segment gets a real SpeechBrain ECAPA-TDNN embedding (pretrained on VoxCeleb), clustered online against per-session voice centroids (cosine threshold 0.72, up to 8 distinct speakers per session). This is genuine, working voice-clustering — it can reliably tell two different people apart within one session.
2. **Identification** (`identify_speaker()`): each diarized cluster's embedding is compared via cosine similarity against every enrolled `VoiceEnrollment` row in the DB (global search, not scoped to the session's logged-in user — anyone enrolled can be matched, not just the account owner). Above `COSINE_THRESHOLD` (0.50, `core/config.py`) → returns that person's real name/role. Below → falls through to the identity resolver's fallback logic.
3. **Fallback labeling** (`IdentityResolverWorker`): the pipeline remembers which diarized cluster was heard *first* in a session (presumed to be that session's owner). Only that cluster falls back to `"You"` when unmatched; any other distinct voice that fails to match is labeled `"Unknown speaker (spk-N)"` instead — so a second, real person is never silently mislabeled as the session owner just because identification failed for both of them.

## The embedding model (important history)

`EmbedProvider.extract()` now uses the same real SpeechBrain ECAPA-TDNN model as the diarizer (loaded independently, since enrollment/identification and in-session clustering are deliberately separate lifecycles). It previously used a hand-rolled pitch/Mel-spectrum feature extractor whose final 512-dim projection was derived from a **random matrix seeded by that specific utterance's own content** — meaning two different recordings of the same person landed in unrelated, incomparable vector spaces almost every time. This was the actual root cause of genuinely-enrolled speakers (other than whoever happened to match by chance) failing to be recognized. That old extractor is kept only as a last-resort fallback (`_extract_legacy_unreliable`) if `torch`/`speechbrain` aren't installed in a given environment — never the intended path.

**Consequence**: every enrollment made before this fix is stored in the old, incompatible embedding format (1024 or 2048 bytes) and can never match the new model's 192-dim embeddings (`cosine_similarity()` treats a dimension mismatch as "definitely not a match," never crashes). Anyone enrolled before this fix needs to **re-enroll their voice once** for identification to work for them again.

## Security note

The identity match also gates a real authorization check, not just display labeling — `backend/pipeline/asr_worker.py`'s "Latch window" logic blocks a pending sensitive action from being voice-confirmed by anyone other than whoever originally triggered it, keyed off `speaker_id`. This is why `COSINE_THRESHOLD` isn't just a UX tuning knob: lowering it improves recognition of legitimately enrolled people but also raises the risk of a false accept (matching the wrong person's voice as this one's owner) for that same check.
