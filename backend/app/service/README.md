# Service Layer

**Navigation:** [App overview](../README.md) | [API routers](../api/README.md) | [Core utilities](../core/README.md) | [Backend docs](../../docs/README.md) | [Tests](../../tests/README.md) | [Models (Beam)](../../../Models/README.md)

These files are the “workers” who do the actual tasks after the API routers greet a request. Each file focuses on one job so the code stays understandable.

## Modules (non-technical + technical)
- `auth_service.py`
  - **Non-technical:** Creates and verifies user accounts safely.
  - **Technical:** Validates input, hashes passwords with bcrypt, reads/writes users in AstraDB, raises `AuthenticationError` for router mapping.
- `text_extractor.py`
  - **Non-technical:** Opens PDFs/Word/TXT files and pulls out the text.
  - **Technical:** Dispatches by MIME type using PyMuPDF and python-docx; returns UTF-8 strings.
- `chunker.py` and `chunk_polisher.py`
  - **Non-technical:** Break long text into small, neat pieces so search is fast and accurate.
  - **Technical:** Size-bounded segmentation plus whitespace/character cleanup to fit Astra limits.
- `embedder.py`
  - **Non-technical:** Converts text chunks into numbers so the system can compare meanings.
  - **Technical:** Calls Beam embedding endpoint, expecting `{"embedding": [[...]]}` vectors.
- `vector_store.py` and `vectordb_init.py`
  - **Non-technical:** Saves those numeric chunks in a database and can find the most similar ones later.
  - **Technical:** Initializes Astra collections, upserts documents, and performs cosine similarity search.
- `query_refiner.py`
  - **Non-technical:** Rephrases user questions to be clearer before searching.
  - **Technical:** Calls the Beam query-refiner model; returns a short, search-friendly string.
- `answer_generator.py`
  - **Non-technical:** Crafts the final answer using the best-matching text and the original question.
  - **Technical:** Sends context and query to the Beam answer model; expects `{"answer": ...}`.
- `beam_client.py`
  - **Non-technical:** A direct line to the AI services for scripts.
  - **Technical:** Legacy synchronous client for Beam endpoints.

## Typical flows
- **Ingestion:** `router_ingest` -> `text_extractor` -> `chunker` -> `chunk_polisher` -> `embedder` -> `vector_store.upsert_chunk`.
- **Query/RAG:** `router_query` -> `query_refiner` -> `embedder` -> `vector_store.search_similar_chunks` -> `answer_generator`.
- **Auth:** `router_auth` -> `auth_service` using `core` helpers.

Keep configuration (`.env`) synchronized with Beam and Astra endpoints. Non-technical: this is the operations crew that gets work done. Technical: make sure environment variables are set so services can reach external dependencies. Add new services here and link them so others can find the code paths quickly.
