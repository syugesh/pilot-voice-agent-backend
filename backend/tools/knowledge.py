"""
Knowledge-base search tool — semantic RAG retrieval over support docs.

Thin tool wrapper around services/kb_index (embeddings + FAISS). Keeps the
same {status, results} return contract the old keyword stub had, so the
existing bg-agent reply path and toolCards timeline keep working — but each
result now carries a similarity score and doc_id for grounded citations.
"""
async def kb_search(args: dict, session_id: str) -> dict:
    from services.kb_index import kb_index
    query = args.get("query", "")
    results = await kb_index.search(query, k=3)
    return {"status": "ok", "results": results, "query": query}
