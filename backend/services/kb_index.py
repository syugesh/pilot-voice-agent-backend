"""
Knowledge-base RAG index — semantic retrieval over support/troubleshooting docs.

Replaces the old keyword-substring kb_search with real embedding search:
sentence-transformers (all-MiniLM-L6-v2, ~90MB, CPU-fine) embeds each
KBDocument's title+content once, FAISS holds the vectors in-process, and a
query is embedded and matched by cosine similarity (inner product on
L2-normalized vectors). Returns ranked hits with a similarity score and the
source doc id, so the CSR dashboard can show grounded citations.

The index is built at startup from the KBDocument DB table (seeded on first
run if empty — see SEED_DOCS). Degrades to substring matching if the embedding
model can't load (offline), so retrieval never hard-fails.
"""
import asyncio, logging
import numpy as np
logger = logging.getLogger("pilot.kb_index")

_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# ISP/telecom support corpus — matches the flagship demo domain so a query like
# "internet down for days / DSL blinking amber" retrieves the right procedure.
SEED_DOCS = [
    {"title": "Internet Outage Troubleshooting",
     "content": "If the internet is completely down: 1) Check for a known outage in your area via the status page. 2) Verify all cables between the wall, modem, and router are seated. 3) Power-cycle the modem: unplug for 30 seconds, plug back in, wait 2 minutes for all lights to stabilize. 4) If the modem's internet/DSL light stays red or off after reboot, the line is not syncing and the issue is upstream — escalate to L2 for a line test. Outages lasting more than 24 hours with a red sync light almost always require an L2 technician.",
     "tags": "outage,internet,down,connectivity,l2"},
    {"title": "DSL / Modem Light Meanings",
     "content": "Power light: solid green = powered. DSL/Line light: solid green = synced to the exchange; blinking amber/orange = the modem is trying to sync but failing (line fault, wiring, or exchange issue); off/red = no signal. Internet light: solid = online; off = authenticated but no internet. A persistently blinking amber DSL light after a reboot indicates a sync failure that L1 cannot fix remotely — collect the line stats and escalate to L2.",
     "tags": "dsl,modem,lights,amber,sync,l2"},
    {"title": "Router Reboot Procedure",
     "content": "To reboot the router safely: unplug the power adapter (do not use the reset pinhole — that erases config), wait 30 seconds, plug back in, and allow up to 3 minutes for Wi-Fi to come back. If the customer has already rebooted multiple times without success, do not ask them to reboot again — repeated failed reboots are an escalation signal, not a fix. Move to line diagnostics or L2.",
     "tags": "router,reboot,restart,wifi"},
    {"title": "Billing and Refund Policy",
     "content": "Service credits for outages: customers are eligible for a pro-rated credit for any full day of confirmed outage. A refund or credit can be issued by L1 up to 30 dollars; amounts above that require manager approval. Disputed charges should be logged as a billing ticket with the disputed amount and date. Do not promise a specific refund amount before the outage is confirmed in the system.",
     "tags": "billing,refund,credit,outage,dispute"},
    {"title": "Escalation Policy (L1 to L2)",
     "content": "Escalate from L1 to L2 when ANY of the following is true: the outage has lasted more than 24 hours; the modem DSL/sync light is red or blinking amber after a verified reboot; the customer has made three or more prior contacts about the same issue; a line test is required; or the customer is highly frustrated and L1 has exhausted the standard troubleshooting script. Escalations must include a one-line issue summary and the troubleshooting already attempted so L2 does not repeat L1 steps.",
     "tags": "escalation,l1,l2,policy,technician"},
    {"title": "Account and Login Support",
     "content": "For login problems: confirm the account email, send a password reset (OTP valid 10 minutes), and verify the account is active and not suspended for non-payment. A suspended account will fail login silently — check billing status before troubleshooting the password. Repeated failed logins after a successful reset indicate a suspended or locked account, not a password issue.",
     "tags": "account,login,password,reset,suspended"},
    {"title": "Slow Speed Troubleshooting",
     "content": "For slow but working internet: run a wired speed test directly on the router to rule out Wi-Fi interference; compare the result to the subscribed plan speed. If wired speed is far below plan, it is a line/provisioning issue — escalate. If wired speed matches the plan but Wi-Fi is slow, the issue is in-home Wi-Fi (distance, interference, old device) and is not an escalation — advise on router placement and channel.",
     "tags": "slow,speed,wifi,performance"},
]


class KBIndex:
    def __init__(self):
        self._model = None
        self._index = None            # faiss index
        self._docs: list[dict] = []   # aligned with index rows: {id, title, content, tags}
        self._loaded = False
        self._available = False

    def _load_model(self):
        if self._model is not None or not self._available_possible():
            return
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(_EMBED_MODEL)
            self._available = True
            logger.info(f"KB embedder ready → {_EMBED_MODEL}")
        except Exception as e:
            logger.warning(f"KB embedder unavailable ({e}) — substring fallback")
            self._model = None
            self._available = False

    def _available_possible(self) -> bool:
        return True

    def _embed(self, texts: list[str]) -> np.ndarray:
        vecs = self._model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return vecs.astype("float32")

    async def build_from_db(self):
        """Load all KBDocument rows (seeding first if the table is empty),
        embed them, and build the FAISS index. Called once at startup."""
        from db.engine import AsyncSessionLocal
        from db.models import KBDocument
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(KBDocument))).scalars().all()
            if not rows:
                logger.info("KB table empty — seeding support docs")
                for d in SEED_DOCS:
                    db.add(KBDocument(title=d["title"], content=d["content"], tags=d["tags"]))
                await db.commit()
                rows = (await db.execute(select(KBDocument))).scalars().all()

            self._docs = [{"id": r.id, "title": r.title, "content": r.content, "tags": r.tags or ""} for r in rows]

        self._load_model()
        if self._available and self._docs:
            import faiss
            corpus = [f"{d['title']}. {d['content']}" for d in self._docs]
            vecs = await asyncio.to_thread(self._embed, corpus)
            index = faiss.IndexFlatIP(vecs.shape[1])  # inner product == cosine on normalized vecs
            index.add(vecs)
            self._index = index
            logger.info(f"KB index built: {len(self._docs)} docs, dim={vecs.shape[1]}")
        else:
            self._index = None
            logger.info(f"KB index in substring-fallback mode ({len(self._docs)} docs)")
        self._loaded = True

    def _substring_search(self, query: str, k: int) -> list[dict]:
        q = query.lower()
        scored = []
        for d in self._docs:
            hay = (d["title"] + " " + d["content"] + " " + d["tags"]).lower()
            hits = sum(1 for w in q.split() if len(w) > 2 and w in hay)
            if hits:
                scored.append((hits, d))
        scored.sort(key=lambda x: -x[0])
        return [{"doc_id": d["id"], "title": d["title"], "excerpt": d["content"][:180],
                 "score": round(min(1.0, h / max(1, len(q.split()))), 3)}
                for h, d in scored[:k]]

    def _semantic_search(self, query: str, k: int) -> list[dict]:
        qv = self._embed([query])
        scores, idxs = self._index.search(qv, min(k, len(self._docs)))
        out = []
        for score, idx in zip(scores[0], idxs[0]):
            if idx < 0:
                continue
            d = self._docs[idx]
            out.append({"doc_id": d["id"], "title": d["title"],
                        "excerpt": d["content"][:180], "score": round(float(score), 3)})
        return out

    async def search(self, query: str, k: int = 3) -> list[dict]:
        if not self._loaded:
            await self.build_from_db()
        query = (query or "").strip()
        if not query or not self._docs:
            return []
        if self._available and self._index is not None:
            return await asyncio.to_thread(self._semantic_search, query, k)
        return self._substring_search(query, k)


kb_index = KBIndex()
