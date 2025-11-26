# Query & RAG

[Feature index](./README.md) | [Authentication](./authentication.md) | [Ingestion pipeline](./ingestion-pipeline.md) | [Query & RAG](./query-pipeline.md) | [Frontend register](./frontend-register.md) | [Frontend workspace](./frontend-workspace.md)

Backend feature that powers semantic search and answer generation over ingested documents.

## Purpose
- Accept user questions, refine them with an LLM, embed the refined query, retrieve similar chunks, and generate a grounded answer.

## Key code
- Router + models: `backend/app/api/router_query.py`
- Refinement: `backend/app/service/query_refiner.py`
- Embeddings: `backend/app/service/embedder.py`
- Vector search: `backend/app/service/vector_store.py`
- Answer generation: `backend/app/service/answer_generator.py`

## API contracts
- `GET /api/health` (via router) - returns `{"query_service":"ok"}`.
- `POST /api/query` - body `{ query, top_k? }`; returns `{ answer }`; 500s surface refinement/embed/search errors.
- `POST /api/query/direct` - same body; skips refinement for debugging.

## Flow
1) `refine_query()` sends the raw question to the Beam query-refiner model (`Model_Query_LLM`).
2) `embed_text()` turns the refined string into a 768-dim vector.
3) `search_similar_chunks()` runs Astra vector sort and computes cosine similarity to rank results.
4) Top chunks are fed to `generate_answer()` which calls the Beam answer generator (`Model_AnswerGenerator_LLM`).
5) Router returns the answer (chunk metadata can be re-enabled in the response model if needed).

## Configuration
- Beam URLs/keys in `backend/.env`: `BEAM_REFINE_LLM_URL`, `BEAM_REFINE_LLM_KEY`, `BEAM_ANSWER_GENERATOR_LLM_URL`, `BEAM_ANSWER_GENERATOR_LLM_KEY`.
- Astra credentials shared with other services.

## Testing & debugging
- Manual assertions live in `backend/tests/test_query_manual.py`; extend with automated RAG tests once endpoints are stable.
- Watch router logs for refinement/embedding failures; add retries/backoff before production.

## Integration
- Frontend `MainPage` calls `POST {VITE_API_BASE}/api/query` when the user sends a message.
- Models backing this flow are documented in `Models/README.md` plus the per-model READMEs.
