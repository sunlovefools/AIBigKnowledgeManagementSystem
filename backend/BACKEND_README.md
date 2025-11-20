# Backend Documentation (FastAPI + AstraDB + Beam)

This document explains the backend located in `backend/`. It covers API architecture, services, data pipelines, environment variables, and operational workflows so any teammate can develop, test, or deploy the FastAPI server with confidence.

---

## Tech Stack

| Layer | Technology | Notes |
|-------|------------|-------|
| Web Framework | FastAPI 0.111 (Python 3.11+) | Async endpoints, Pydantic validation, CORS middleware. |
| Persistence | Astra DB (Data API) | Stores auth users + vectorized document chunks. |
| Vector Search | `astrapy` collection with cosine metric (dim 768) | Accessed through `vectordb_init.py` + `vector_store.py`. |
| AI Services | Beam-hosted endpoints | Query refiner, embedding model, answer generator, plus general LLMs. |
| File Processing | PyMuPDF (`fitz`) + `python-docx` | Extracts structured text from PDFs/Word docs. |
| Background Ops | Ingestion is synchronous for now; easy to move to task queue later. |

---

## Directory Layout

```
backend/
├── app/
│   ├── main.py                 # FastAPI app factory, routers, startup hooks
│   ├── api/
│   │   ├── router_auth.py      # /auth endpoints (register/login/health)
│   │   ├── router_ingest.py    # /ingest/webhook for uploads + chunk pipeline
│   │   └── router_query.py     # /query endpoints powering the RAG flow
│   ├── core/
│   │   ├── password_utils.py   # bcrypt hashing helpers
│   │   └── validation.py       # email/password validators & sanitizers
│   └── service/
│       ├── auth_service.py         # Astra-backed user storage
│       ├── text_extractor.py       # PDF/DOC/TXT parsing
│       ├── chunker.py              # Paragraph/sentence chunking
│       ├── chunk_polisher.py       # Normalizes chunk text
│       ├── embedder.py             # Calls Beam embeddings endpoint (async)
│       ├── vector_store.py         # Upserts/searches Astra collection
│       ├── vectordb_init.py        # Creates `document_chunks_2` collection
│       ├── beam_client.py          # Legacy HTTP client for LLM + embed services
│       ├── query_refiner.py        # Hits Beam query-refiner endpoint
│       ├── answer_generator.py     # Hits Beam RAG answer endpoint
│       ├── chunker/… utilities     # (future) retrieval logic lives here
│       └── tmp/                    # Temporary upload artifacts
├── docs/                       # Feature-specific guides (ingestion, query, vector DB)
├── tests/                      # Pytest skeleton
├── requirements.txt            # Runtime deps
├── Dockerfile                  # FastAPI container for deployment
└── .env                        # Local secrets (never commit real credentials)
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

# (Optional) Additional Beam services
BEAM_LLM_URL=...
BEAM_LLM_KEY=...
BEAM_REFINE_LLM_URL=https://api.beam.cloud/v1/qwen-1_5b-query-refiner
BEAM_REFINE_LLM_KEY=<bearer>
BEAM_ANSWER_GENERATOR_LLM_URL=https://api.beam.cloud/v1/qwen-1_5b-answer-generator
BEAM_ANSWER_GENERATOR_LLM_KEY=<bearer>

# Timeout helpers
BEAM_TIMEOUT=60
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
| `/auth/register` | POST | Creates user in Astra (`AuthService.register_user`) | `router_auth` |
| `/auth/login` | POST | Validates credentials (bcrypt) | `router_auth` |
| `/ingest/health` | GET | Ingestion subsystem status | `router_ingest` |
| `/ingest/webhook` | POST | Accepts `{fileName, contentType, data(base64)}`; runs ingestion pipeline | `router_ingest` |
| `/query/health` | GET | Query subsystem status | `router_query` |
| `/query` | POST | Full RAG pipeline (refine -> embed -> vector search -> answer) | `router_query` |
| `/query/direct` | POST | Vector search without refinement (debug) | `router_query` |

Authentication & authorization are still lightweight (no JWT); responses omit password hashes and include simple status messages.

---

## Data Pipelines

### 1. Document Ingestion (`POST /ingest/webhook`)

1. **Payload intake** – `router_ingest.FileUpload` receives filename, MIME type, and base64 body (temporary stand-in for actual MinIO webhook event).
2. **Text extraction** – `text_extractor.extract_text()` dispatches by MIME:  
   - `application/pdf` → PyMuPDF  
   - `application/msword` / `application/vnd.openxmlformats-officedocument.wordprocessingml.document` → `python-docx`  
   - `text/plain` → UTF-8 decode
3. **Chunking** – `chunker.split_into_chunks()` merges paragraphs into ~1000 char windows; long paragraphs fall back to sentence splits.
4. **Polishing** – `chunk_polisher.polish_chunks()` removes stray whitespace, bullet characters, and normalizes punctuation/casing.
5. **Embedding** – Build payload `{"input": ["chunk text", ...]}` and call the Beam embedding endpoint through `embed_text()` (async `aiohttp`).  
   The response is expected as `{"embedding": [[float...], ...]}` where vector dimension = 768.
6. **Vector DB Upsert** – For each chunk attach metadata (`document_name`, `chunk_number`, `uploaded_by`, `timestamp`) and call `vector_store.upsert_chunk()`, which writes to the Astra collection initialized via `vectordb_init.init_vector_db()`.

Intermediate debug dumps (`vectors_debug.txt`, `polished_chunks_debug.txt`) are written to root for troubleshooting. Remove or guard them behind feature flags before production.

### 2. Query + Retrieval-Augmented Generation (`POST /query`)

1. **Refinement** – `query_refiner.refine_query()` posts the raw question to the Beam Query Refiner (Model_Query_LLM). Returns a single-sentence rephrase optimized for embeddings.
2. **Query embedding** – `embed_text()` (same as ingestion) converts the refined string to a 768-dim vector.
3. **Vector search** – `vector_store.search_similar_chunks()` sorts Astra collection by `$vector` similarity (cosine) and returns metadata for `top_k` chunks. `include_similarity=True` is leveraged to read `$similarity`.
4. **Answer generation** – Extract the textual chunk list and call `answer_generator.generate_answer()` which posts `{"rag_context": "<chunks...>", "user_query": "<original>"}` to the Beam Answer LLM. Result JSON must contain `answer`.
5. **Response** – Current response schema is simplified to `{"answer": "<LLM output>"}` (can re-enable chunk metadata by uncommenting code in `router_query.py`).

`/query/direct` bypasses the refinement stage for debugging embeddings or the vector store.

---

## Authentication Flow

`AuthService` encapsulates Astra Data API calls:

- `register_user(email, password, role)`  
  - Sanitizes + validates email/password.  
  - Enforces role ∈ {user, admin}.  
  - Hashes password with bcrypt (`password_utils`).  
  - Inserts into `users` collection and returns metadata.

- `login_user(email, password)`  
  - Fetches by sanitized email.  
  - Confirms `is_active`.  
  - Verifies bcrypt hash.  
  - Returns limited profile (no hash).  

Error handling uses custom `AuthenticationError` which `router_auth` translates into appropriate HTTP status codes (400/401/409/503).

---

## Supporting Services

- **`beam_client.py`** – Legacy synchronous helper to call Beam-hosted LLM + embed endpoints directly (`/generate`, `/embed`). Useful for scripts or fallback flows.
- **`answer_generator.py`** – Async wrapper for the dedicated Beam answer endpoint (Model_AnswerGenerator_LLM). Accepts list of chunk texts + user query.
- **`query_refiner.py`** – Async call to Model_Query_LLM (Beam) to rewrite user queries prior to embedding.
- **`vector_store.py`** – Centralized operations on Astra collection (insert + similarity search). Includes manual `cosine_similarity` helper for debugging.
- **`text_extractor.py`** – Handles PDF/Word/TXT ingestion; writes bytes to `/tmp/_tmp.*` to interop with libraries that read from disk.

---

## Testing & Debugging

- Pytest entry point exists at `backend/tests/` (currently empty). Add unit tests for services (chunker, polisher, auth) and integration tests for routers using `httpx.AsyncClient`.
- Debug toggles:
  - `vectors_debug.txt` / `polished_chunks_debug.txt` capture last run embeddings/chunks.
  - Logs inside routers show emoji-coded stages (replace with structured logging for prod).
- Use FastAPI docs UI or `curl`/Postman to reproduce flows. Example ingestion request:

```bash
curl -X POST http://127.0.0.1:8000/ingest/webhook ^
  -H "Content-Type: application/json" ^
  -d "{\"fileName\":\"demo.pdf\",\"contentType\":\"application/pdf\",\"data\":\"<base64>\"}"
```

---

## Deployment Notes

1. Ensure Astra DB collection `document_chunks_2` exists by letting `init_vector_db()` run during startup (FastAPI `@app.on_event("startup")` already calls it).
2. Beam secrets must be configured in the Beam console and referenced through the environment variables listed earlier.
3. When deploying behind HTTPS, tighten CORS origins in `main.py` (`allow_origins=["https://frontend-host"]`).
4. For scaling:
   - Move ingestion to a task queue or background worker (Celery/RQ) to avoid blocking HTTP requests during large uploads.
   - Cache embeddings for repeated queries.
   - Add JWT authentication (e.g., `fastapi-users` or custom OAuth) so `MainPage` requests carry tokens.

---

## Future Enhancements

- **JWT & RBAC** – Add `/auth/login` token issuance + role-based guards on ingestion/query routes.
- **MinIO Webhook** – Replace mock `/webhook` payload with actual S3-compatible object storage events.
- **Observability** – Plug in structured logging + metrics (Prometheus, OpenTelemetry).
- **Retries** – Wrap Beam/Astra calls with exponential backoff to handle transient failures.
- **Pagination & Metadata** – Expand query response to include chunk provenance displayed in the frontend.
- **Testing** – Flesh out `tests/` with unit + integration coverage, especially for chunking and auth.

Use this reference to onboard new backend contributors or to understand how the RAG stack is wired into AstraDB and Beam services.
