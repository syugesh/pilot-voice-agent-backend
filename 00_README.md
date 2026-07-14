# System Prompts — AI Customer Resolution & Escalation PILOT

Generated from `customer_care_pilot_plan.md`. One system prompt per agent in the pipeline, in execution order.

| File | Agent | Pipeline Phase |
|---|---|---|
| 01_transcript_cleanup_agent.md | Transcript Cleanup & Structuring Agent | Phase 1 |
| 02_sentiment_frustration_agent.md | Sentiment & Frustration Detection Agent | Phase 2 |
| 03_knowledge_retrieval_rag_agent.md | Knowledge Retrieval (RAG) Agent | Phase 3–4 |
| 04_summary_agent.md | AI Summary Agent | Phase 5 |
| 05_resolution_confidence_engine.md | Resolution Confidence Engine | Phase 6 |
| 06_escalation_recommendation_agent.md | Escalation Recommendation Agent | Phase 7 |
| 07_csr_dashboard_assistant.md | CSR Dashboard Assistant (human-facing) | Phase 8 |
| 08_master_orchestrator_agent.md | Master Orchestrator Agent | Phase 9 |

## Design principles applied across all prompts

- **Strict grounding:** RAG, Summary, and Escalation agents are explicitly forbidden from fabricating facts, doc_ids, or policy rules not present in their input.
- **Separation of concerns:** Each agent has a narrow, non-overlapping responsibility (e.g., the Confidence Engine scores but never decides escalation; the Escalation Agent decides but never talks to the customer).
- **Structured I/O:** Every internal agent (01–06) outputs JSON only, consumed by the next stage — only the CSR Dashboard Assistant (07) produces human-readable output.
- **Graceful degradation:** The Orchestrator (08) and Dashboard (07) prompts both specify explicit handling for missing/failed upstream stages instead of guessing.
- **Auditability:** Confidence and Escalation agents must always expose their reasoning/factor breakdown, never a bare score.

## Usage

Load the corresponding `.md` file as the `system` message for each agent's LLM call (e.g., Phi-3 Mini / Gemma 2B / Qwen 2.5 3B per the pilot plan's stack). Pass the JSON input/output contracts defined in each file between pipeline stages as implemented by the Master Orchestrator.
