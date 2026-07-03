"""
Diarizer — streaming speaker identification.

Streaming path (preferred):
  TurnSegment arrives with embed_futures: per-window asyncio Tasks that were
  launched during VAD as speech frames accumulated.  Each task returns
  (embedding: np.ndarray, quality: float).  The worker awaits all futures,
  identifies the speaker for each window, and majority-votes to label the turn.

Batch fallback:
  If embed_futures is empty (very short turn, or model not loaded) the worker
  falls back to extracting one embedding from the full turn PCM.

Both paths write a LabeledTurn → identity_q.
"""
import asyncio, logging
import numpy as np
from queues.bus import QueueBus, TurnSegment, LabeledTurn

logger = logging.getLogger("pilot.diarizer")

# Await each streaming window task for at most this long.
# Embedding extraction is ~50–100 ms on CPU, so 1.5 s is generous.
_WINDOW_TIMEOUT_S = 1.5


class DiarizerWorker:
    def __init__(self, bus: QueueBus):
        self.bus = bus

    async def run(self):
        logger.info("Diarizer worker started (streaming + batch fallback)")
        while True:
            seg: TurnSegment = await self.bus.diar_q.get()
            try:
                label, speaker_id, role, confidence = await self._identify(seg)
            except Exception as e:
                logger.error(f"Diarizer error: {e}", exc_info=True)
                label, speaker_id, role, confidence = "spk-0", None, None, 0.0

            labeled = LabeledTurn(
                pcm=seg.pcm, session_id=seg.session_id,
                timestamp=seg.timestamp, speaker_label=label,
                speaker_id=speaker_id, role=role, confidence=confidence,
            )
            await self.bus.identity_q.put(labeled)

    # ── Main dispatch ─────────────────────────────────────────────────────────

    async def _identify(self, seg: TurnSegment) -> tuple[str, str | None, str | None, float]:
        if seg.embed_futures:
            return await self._streaming_identify(seg)
        return await self._batch_identify(seg)

    # ── Streaming path ────────────────────────────────────────────────────────

    async def _streaming_identify(self, seg: TurnSegment) -> tuple[str, str | None, str | None, float]:
        from services.enrollment import identify_embedding

        window_results: list[tuple[str | None, str | None, float]] = []

        for win_idx, (start_sec, task) in enumerate(seg.embed_futures):
            try:
                embedding, quality = await asyncio.wait_for(task, timeout=_WINDOW_TIMEOUT_S)
                speaker_id, role, conf, msg = await identify_embedding(embedding, quality)
                window_results.append((speaker_id, role, conf))
                logger.info(
                    f"[{seg.session_id[:8]}] window {win_idx} "
                    f"@{start_sec:.2f}s → {speaker_id!r} conf={conf:.2f}"
                )
            except asyncio.TimeoutError:
                logger.warning(f"[{seg.session_id[:8]}] window {win_idx} embed timeout")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"[{seg.session_id[:8]}] window {win_idx} error: {e}")

        if not window_results:
            logger.debug(f"[{seg.session_id[:8]}] no window results — batch fallback")
            return await self._batch_identify(seg)

        return _majority_vote(window_results, seg.session_id)

    # ── Batch fallback ────────────────────────────────────────────────────────

    async def _batch_identify(self, seg: TurnSegment) -> tuple[str, str | None, str | None, float]:
        from services.enrollment import identify_speaker
        try:
            speaker_id, role, conf, msg = await identify_speaker(seg.pcm)
            label = "spk-0" if not speaker_id else f"spk-{speaker_id}"
            logger.info(
                f"[{seg.session_id[:8]}] batch identify → {speaker_id!r} "
                f"cosine={int(conf*100)}% ({msg})"
            )
            return label, speaker_id, role, conf
        except Exception as e:
            logger.error(f"Batch identify error: {e}")
            return "spk-0", None, None, 0.0


# ── Majority vote across streaming windows ────────────────────────────────────

def _majority_vote(
    results: list[tuple[str | None, str | None, float]],
    session_id: str,
) -> tuple[str, str | None, str | None, float]:
    """
    Weighted majority vote across per-window speaker IDs.
    Weight = confidence score.  Returns (label, speaker_id, role, avg_confidence).
    """
    # Accumulate confidence per (speaker_id, role) pair
    scores: dict[tuple, float] = {}
    for speaker_id, role, conf in results:
        key = (speaker_id, role)
        scores[key] = scores.get(key, 0.0) + conf

    best_key = max(scores, key=scores.get)
    best_speaker_id, best_role = best_key
    total_conf = sum(scores.values())
    win_conf   = scores[best_key]
    # Normalise: winner's share of total confidence
    avg_conf = win_conf / max(total_conf, 1e-6) * (sum(c for _, _, c in results) / len(results))

    label = "spk-0" if not best_speaker_id else f"spk-{best_speaker_id}"
    cosine_pct = int(min(avg_conf, 1.0) * 100)
    logger.info(
        f"[{session_id[:8]}] streaming vote → {best_speaker_id!r} "
        f"cosine={cosine_pct}% "
        f"({len(results)} windows, win_share={win_conf/max(total_conf,1e-6):.0%})"
    )
    return label, best_speaker_id, best_role, min(avg_conf, 1.0)
