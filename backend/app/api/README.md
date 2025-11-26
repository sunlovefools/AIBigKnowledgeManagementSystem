# API Routers

**Navigation:** [App overview](../README.md) | [Service layer](../service/README.md) | [Core utilities](../core/README.md) | [Backend docs](../../docs/README.md) | [Frontend register](../../../../frontend/src/pages/register/README.md) | [Frontend workspace](../../../../frontend/src/pages/mainpage/README.md)

These files define the URLs the frontend calls. Each router is a small receptionist: it greets the request, checks the form, and hands it to the right worker in `service/`.

## Files and what they do
- `router_auth.py`
  - **What users see:** sign up and log in.
  - **Endpoints:** `GET /auth/health`, `POST /auth/register`, `POST /auth/login`.
  - **Behind the scenes:** validates emails/passwords, hashes passwords, and saves/reads users from AstraDB via `AuthService`.
- `router_ingest.py`
  - **What users see:** upload a file.
  - **Endpoints:** `GET /ingest/health`, `POST /ingest/webhook` (accepts base64 file data).
  - **Behind the scenes:** extracts text, chops it into chunks, creates embeddings, and stores them for search using `text_extractor`, `chunker`, `embedder`, and `vector_store`.
- `router_query.py`
  - **What users see:** ask a question and get an answer.
  - **Endpoints:** `GET /api/health`, `POST /api/query`, `POST /api/query/direct`.
  - **Behind the scenes:** refines the question, generates embeddings, searches similar chunks, and asks the answer generator model to respond.

## How a request is handled
1) The router reads and validates the incoming JSON (e.g., `{ email, password }` or `{ fileName, data }`).  
2) It calls the matching function in `../service/` to do the real work.  
3) If there’s an error, the router translates it into a clear HTTP status code and message.  
4) A clean, structured response is sent back to the frontend.

Non-technical takeaway: this folder is the “front desk.” Technical takeaway: each router is thin on purpose; keep logic in `service/` and validation helpers in `core/`.
