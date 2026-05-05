# Backend Documentation (FastAPI + AstraDB + Beam/Ollama)

This document explains the backend located in `backend/`. It covers API architecture, services, data pipelines, environment variables, and operational workflows so any teammate can develop, test, or deploy the FastAPI server with confidence.

---

## Tech Stack

| Layer | Technology | Notes |
|-------|------------|-------|
| Web Framework | FastAPI 0.111 (Python 3.11+) | Async endpoints, Pydantic validation, CORS middleware. |
| Persistence | Astra DB (Data API) | Stores auth users + vectorized document chunks. |
| Vector Search | `astrapy` collection with cosine metric (dim 768) | Accessed through `vectordb_init.py` + `vector_store.py`. |
| AI Services | Beam + Ollama + OpenRouter endpoints | Query refiner/embeddings via Beam and answer generation via provider-routed Ollama/BEAM/OpenRouter with structured context blocks. |
| File Processing | PyMuPDF (`fitz`) + `python-docx` | Extracts structured text from PDFs/Word docs. |
| Background Ops | Ingestion is synchronous for now; easy to move to task queue later. |

---

## Directory Layout

```
backend/
â”œâ”€â”€ app/
â”‚   â”œâ”€â”€ main.py                 # FastAPI app factory, routers, startup hooks
â”‚   â”œâ”€â”€ api/
â”‚   â”‚   â”œâ”€â”€ router_auth.py      # /auth endpoints (register/login/health)
â”‚   â”‚   â”œâ”€â”€ router_ingest.py    # /ingest/upload for uploads + chunk pipeline
â”‚   â”‚   â””â”€â”€ router_query.py     # /query endpoints powering the RAG flow
â”‚   â”œâ”€â”€ core/
â”‚   â”‚   â”œâ”€â”€ password_utils.py   # bcrypt hashing helpers
â”‚   â”‚   â””â”€â”€ validation.py       # email/password validators & sanitizers
â”‚   â””â”€â”€ service/
â”‚       â”œâ”€â”€ auth_service.py         # Astra-backed user storage
â”‚       â”œâ”€â”€ text_extractor.py       # PDF/DOC/TXT parsing
â”‚       â”œâ”€â”€ chunker.py              # Paragraph/sentence chunking
â”‚       â”œâ”€â”€ chunk_polisher.py       # Normalizes chunk text
â”‚       â”œâ”€â”€ embedder.py             # Calls Beam embeddings endpoint (async)
â”‚       â”œâ”€â”€ vector_store.py         # Upserts/searches Astra collection
â”‚       â”œâ”€â”€ vectordb_init.py        # Creates `document_chunks_2` collection
â”‚       â”œâ”€â”€ beam_client.py          # Legacy HTTP client for LLM + embed services
â”‚       â”œâ”€â”€ query_refiner.py        # Hits Beam query-refiner endpoint
â”‚       â”œâ”€â”€ answer_generator.py     # Compatibility facade delegating to answer_generation package
â”‚       â”œâ”€â”€ chunker/â€¦ utilities     # (future) retrieval logic lives here
â”‚       â””â”€â”€ tmp/                    # Temporary upload artifacts
â”œâ”€â”€ docs/                       # Feature-specific guides (ingestion, query, vector DB)
â”œâ”€â”€ tests/                      # Pytest skeleton
â”œâ”€â”€ requirements.txt            # Runtime deps
â”œâ”€â”€ Dockerfile                  # FastAPI container for deployment
â””â”€â”€ .env                        # Local secrets (never commit real credentials)
```

---

## Environment Variables

Create `backend/.env` (already gitignored) with the following keys:

```
# Astra Database
ASTRA_DB_URL=<https://...apps.astra.datastax.com>
ASTRA_DB_TOKEN=<AstraCS:...>

# Beam Embedding Endpoint
BEAM_EMBEDDING_URL=https://embedding-<slug>.app.beam.cloud
BEAM_EMBEDDINGS_KEY=<beam-token>

# Embedding provider/model controls
EMBEDDING_PROVIDER=LOCAL            # LOCAL or BEAM
LOCAL_EMBEDDING_MODEL=google/embeddinggemma-300m
EMBEDDING_SWAP_TO_RAM=false         # LOCAL only; offload embedding model to CPU RAM between calls
EMBEDDING_GPU_INGEST_ONLY=true      # LOCAL only; ingestion on accelerator, query on CPU RAM

# (Optional) Additional Beam services
BEAM_LLM_URL=...
BEAM_LLM_KEY=...
BEAM_REFINE_LLM_URL=https://api.beam.cloud/v1/qwen-1_5b-query-refiner
BEAM_REFINE_LLM_KEY=<bearer>
ANSWER_GENERATOR_LLM_PROVIDER=OLLAMA
# Optional for local daemon; required for non-local Ollama hosts
OLLAMA_ANSWER_GENERATOR_LLM_URL=http://127.0.0.1:11434/api/generate
OLLAMA_ANSWER_GENERATOR_LLM_MODEL=llama3.1:8b
# Optional model fallbacks:
# LOCAL_ANSWER_GENERATOR_LLM_MODEL=llama3.1:8b
# OLLAMA_MODEL=llama3.1:8b

# BEAM provider for answer generator (deterministic URL key)
ANSWER_GENERATOR_LLM_PROVIDER=BEAM    # Alias to OLLAMA provider path
BEAM_ANSWER_GENERATOR_LLM_URL=http://127.0.0.1:11434/api/generate
BEAM_ANSWER_GENERATOR_LLM_KEY=<beam-token>

# Optional compatibility key alias for BEAM
LOCAL_ANSWER_GENERATOR_LLM_KEY=

# Timeout helpers
BEAM_TIMEOUT=60

# (Optional) Docling artifact/debug outputs (default true)
DOCLING_ARTIFACTS_ENABLED=true
TABLE_SEMANTIC_INGESTION_ENABLED=true
TABLE_SEMANTIC_LLM_URL=https://api.deepseek.com/v1/chat/completions
TABLE_SEMANTIC_LLM_API_KEY=<deepseek-or-openai-compatible-key>
TABLE_SEMANTIC_CLASSIFIER_MODEL=deepseek-chat
TABLE_SEMANTIC_GLOBAL_MODEL=deepseek-chat
TABLE_SEMANTIC_ROW_MODEL=deepseek-chat
TABLE_SEMANTIC_TIMEOUT_S=60
TABLE_SEMANTIC_MAX_SAMPLE_ROWS=8

```

Load them via `python-dotenv` (already invoked inside modules) or export them in the hosting environment.

---

## Local Development

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate    # or source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

- FastAPI docs: `http://127.0.0.1:8000/docs`
- Health probes: `/hello`, `/auth/health`, `/ingest/health`, `/query/health`

Docker workflow:

```bash
docker build -t team44-backend .
docker run --env-file .env -p 8000:8000 team44-backend
```

---

## API Surface

| Route | Method | Description | Handler |
|-------|--------|-------------|---------|
| `/hello` | GET | Simple backend ping | `main.py` |
| `/auth/health` | GET | Auth subsystem status | `router_auth` |
| `/auth/auth0-login` | POST | Exchanges Auth0 access token for internal JWT and user session | `router_auth` |
| `/ingest/health` | GET | Ingestion subsystem status | `router_ingest` |
| `/ingest/upload` | POST | Accepts `{fileName, contentType, data(base64)}`; runs ingestion pipeline | `router_ingest` |
| `/query/health` | GET | Query subsystem status | `router_query` |
| `/query` | POST | Full RAG pipeline (refine -> embed -> vector search -> answer) | `router_query` |
| `/query/direct` | POST | Vector search without refinement (debug) | `router_query` |

Authentication is Auth0-only for sign-in; backend issues internal JWT tokens after `/auth/auth0-login`.

---

## Data Pipelines

### 1. Document Ingestion (`POST /ingest/upload`)

1. **Payload intake** â€“ `router_ingest.FileUpload` receives filename, MIME type, and base64 body (temporary stand-in for actual MinIO webhook event).
2. **Text extraction** â€“ `text_extractor.extract_text()` dispatches by MIME:  
   - `application/pdf` â†’ PyMuPDF  
   - `application/msword` / `application/vnd.openxmlformats-officedocument.wordprocessingml.document` â†’ `python-docx`  
   - `text/plain` â†’ UTF-8 decode
   - Optional PDF strategy switch: set `INGEST_PDF_EXTRACTOR=docling` to use the Beam-hosted Docling conversion endpoint for markdown extraction (default remains `legacy`)
   - Beam Docling endpoint mode requires:
     - `BEAM_DOCLING_ENDPOINT`
     - `BEAM_DOCLING_ENDPOINT_TOKEN`
   - Beam Docling endpoint failures are fail-fast (no automatic fallback to local Docling conversion)
3. **Chunking** â€“ `chunker.split_into_chunks()` merges paragraphs into ~1000 char windows; long paragraphs fall back to sentence splits.
4. **Polishing** â€“ `chunk_polisher.polish_chunks()` removes stray whitespace, bullet characters, and normalizes punctuation/casing.
5. **Embedding** â€“ Build payload `{"input": ["chunk text", ...]}` and call the Beam embedding endpoint through `embed_text()` (async `aiohttp`).  
   The response is expected as `{"embedding": [[float...], ...]}` where vector dimension = 768.
6. **Vector DB Upsert** â€“ For each chunk attach metadata (`document_name`, `chunk_number`, `uploaded_by`, `timestamp`) and call `vector_store.upsert_chunk()`, which writes to the Astra collection initialized via `vectordb_init.init_vector_db()`.

Intermediate debug dumps (`vectors_debug.txt`, `polished_chunks_debug.txt`) are written to root for troubleshooting. Remove or guard them behind feature flags before production.

### 1A. PDF Strategy Switching (`INGEST_PDF_EXTRACTOR`)

- `INGEST_PDF_EXTRACTOR=legacy` (default): PDFs are processed by legacy text extraction + legacy chunking + chunk polishing.
- `INGEST_PDF_EXTRACTOR=docling`: PDFs are processed through Docling parse + Docling structured-block chunking.
- Non-PDF files always use the legacy extraction path.
- Docling errors are fail-fast (no fallback to legacy when strategy is `docling`).
- Docling chunk outputs preserve visual metadata (`content_flags`, `artifact_refs`) during vector upsert.

### 2. Query + Retrieval-Augmented Generation (`POST /query`)

1. **Refinement** â€“ `query_refiner.refine_query()` posts the raw question to the Beam Query Refiner (Model_Query_LLM). Returns a single-sentence rephrase optimized for embeddings.
2. **Query embedding** â€“ `embed_text()` (same as ingestion) converts the refined string to a 768-dim vector.
3. **Vector search** â€“ `vector_store.search_similar_chunks()` sorts Astra collection by `$vector` similarity (cosine) and returns metadata for `top_k` chunks. `include_similarity=True` is leveraged to read `$similarity`.
4. **Answer generation** - Extract the textual chunk list and call `answer_generator.generate_answer()` (compatibility facade). The facade delegates to `answer_generation/orchestration.py`, which resolves provider-specific logic. Context is rendered as numbered blocks inside `<CONTEXT>` for both Ollama/BEAM and OpenRouter provider paths, and only includes `file_name` + `page_content` (no `id`, `type`, or full `metadata`). If Ollama target is local (`localhost` / `127.0.0.1` / `::1`, or URL omitted), the provider uses Python `ollama` (`AsyncClient`) directly. For non-local Ollama/BEAM targets, it calls Ollama `/api/generate` with `model`, `system`, `prompt`, and `stream=false`; response is read from `response`. OpenRouter uses chat-completions `messages` payload with the same context block format.
5. **Response** â€“ Current response schema is simplified to `{"answer": "<LLM output>"}` (can re-enable chunk metadata by uncommenting code in `router_query.py`).

`/query/direct` bypasses the refinement stage for debugging embeddings or the vector store.

---

## Authentication Flow

`AuthService` encapsulates Astra Data API calls:

- `auth0_login(token)`  
  - Verifies Auth0 token using tenant JWKS, audience, and issuer checks.  
  - Resolves `sub` and `email` claims (falls back to Auth0 `/userinfo` when email is absent).  
  - Delegates to `oauth_login(email, oauth_sub)` for account lookup and auto-provisioning.

- `oauth_login(email, oauth_sub)`  
  - Logs in existing OAuth user by `oauth_sub`.  
  - Creates a new user with `auth_provider="oauth"` and default role `user` when not found.  
  - Issues internal JWT used by protected backend routes.

Error handling uses custom `AuthenticationError` which `router_auth` translates into appropriate HTTP status codes (401/503).

### One-Time Cutover Migration

To remove legacy local-password users during Auth0 cutover:

```bash
python backend/scripts/migrate_auth0_only_remove_local_users.py --dry-run
python backend/scripts/migrate_auth0_only_remove_local_users.py
```

This script deletes users where `auth_provider == "local"` and is safe to run multiple times.

---

## Supporting Services

- **`beam_client.py`** â€“ Legacy synchronous helper to call Beam-hosted LLM + embed endpoints directly (`/generate`, `/embed`). Useful for scripts or fallback flows.
- **`answer_generator.py`** - Compatibility facade that forwards calls to `service/rag/retrieval/answer_generation/orchestration.py`.
- **`answer_generation/` package** - Modular answer generation implementation split across config, normalization, citations, HTTP transport, logging adapter, provider implementations, and orchestration.
- **`query_refiner.py`** â€“ Async call to Model_Query_LLM (Beam) to rewrite user queries prior to embedding.
- **`vector_store.py`** â€“ Centralized operations on Astra collection (insert + similarity search). Includes manual `cosine_similarity` helper for debugging.
- **`text_extractor.py`** â€“ Handles PDF/Word/TXT ingestion; writes bytes to `/tmp/_tmp.*` to interop with libraries that read from disk.

---

## Testing & Debugging

- Pytest entry point exists at `backend/tests/` (currently empty). Add unit tests for services (chunker, polisher, auth) and integration tests for routers using `httpx.AsyncClient`.
- Debug toggles:
  - `vectors_debug.txt` / `polished_chunks_debug.txt` capture last run embeddings/chunks.
  - Logs inside routers show emoji-coded stages (replace with structured logging for prod).
- Use FastAPI docs UI or `curl`/Postman to reproduce flows. Example ingestion request:

```bash
curl -X POST http://127.0.0.1:8000/ingest/upload ^
  -H "Content-Type: application/json" ^
  -d "{\"fileName\":\"demo.pdf\",\"contentType\":\"application/pdf\",\"data\":\"<base64>\"}"
```

---

## Deployment Notes

1. Ensure Astra DB collection `document_chunks_2` exists by letting `init_vector_db()` run during startup (FastAPI `@app.on_event("startup")` already calls it).
2. Configure Beam secrets for query refinement/embeddings and set Ollama answer-generator vars (`OLLAMA_ANSWER_GENERATOR_LLM_URL`, `OLLAMA_ANSWER_GENERATOR_LLM_MODEL`). Model fallbacks are also supported via `LOCAL_ANSWER_GENERATOR_LLM_MODEL` or `OLLAMA_MODEL`.
3. When deploying behind HTTPS, tighten CORS origins in `main.py` (`allow_origins=["https://frontend-host"]`).
4. For scaling:
   - Move ingestion to a task queue or background worker (Celery/RQ) to avoid blocking HTTP requests during large uploads.
   - Cache embeddings for repeated queries.
   - Add JWT authentication (e.g., `fastapi-users` or custom OAuth) so `MainPage` requests carry tokens.

---

## Future Enhancements

- **RBAC hardening** - Keep extending role-based guards on ingestion/query/modification routes.
- **MinIO Webhook** â€“ Replace mock upload payload for `/ingest/upload` with actual S3-compatible object storage events.
- **Observability** â€“ Plug in structured logging + metrics (Prometheus, OpenTelemetry).
- **Retries** â€“ Wrap Beam/Astra calls with exponential backoff to handle transient failures.
- **Pagination & Metadata** â€“ Expand query response to include chunk provenance displayed in the frontend.
- **Testing** â€“ Flesh out `tests/` with unit + integration coverage, especially for chunking and auth.

Use this reference to onboard new backend contributors or to understand how the RAG stack is wired into AstraDB and Beam services.


