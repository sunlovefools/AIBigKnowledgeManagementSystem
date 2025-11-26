# Ingestion Pipeline

[Feature index](./README.md) | [Authentication](./authentication.md) | [Ingestion pipeline](./ingestion-pipeline.md) | [Query & RAG](./query-pipeline.md) | [Frontend register](./frontend-register.md) | [Frontend workspace](./frontend-workspace.md)

Backend feature that turns uploaded files into cleaned text chunks, embeds them, and stores them in AstraDB for retrieval.

## Purpose
- Accept uploads (base64 payloads) via `/ingest/webhook`.
- Extract text from PDF/DOCX/TXT, chunk and polish content, call embeddings, then upsert into the vector store.

## Key code
- Router + entrypoint: `backend/app/api/router_ingest.py`
- Extraction: `backend/app/service/text_extractor.py`
- Chunking + polishing: `backend/app/service/chunker.py`, `backend/app/service/chunk_polisher.py`
- Embeddings: `backend/app/service/embedder.py`
- Vector writes: `backend/app/service/vector_store.py`

## API contracts
- `GET /ingest/health` - returns `{"ingestion":"ok"}`.
- `POST /ingest/webhook` - body `{ fileName, contentType, data }` where `data` is base64 file content; returns 200 on success or 415/500 on failures.

## Flow
1) Decode base64 payload into bytes.
2) `extract_text()` chooses parser by MIME type (PyMuPDF for PDF, python-docx for DOCX, UTF-8 decode for TXT).
3) `split_into_chunks()` limits chunks (~600 chars now) to fit Astra limits; `polish_chunks()` strips noise.
4) `embed_text()` posts chunk texts to Beam embedding endpoint; vectors saved to `vectors_debug.txt` for debugging.
5) `upsert_chunk()` writes `{content, document_name, chunk_number, uploaded_by, embedding, timestamp}` into AstraDB.

## Configuration
- Embeddings require `BEAM_EMBEDDING_URL` and `BEAM_EMBEDDINGS_KEY` in `backend/.env`.
- Astra credentials shared with auth (`ASTRA_DB_URL`, `ASTRA_DB_TOKEN`).
- Temporary uploads live under `backend/_local_uploads/` when testing without MinIO.

## Testing & debugging
- Targeted tests: `backend/tests/test_text_extractor.py`, `test_chunker.py`, `test_chunk_polisher.py`, `test_ingest.py`, `test_all_ingest_modules.py`.
- Debug artifacts: `vectors_debug.txt` and `polished_chunks_debug.txt` capture the last run; clear or guard them for production.

## Integration
- Frontend `MainPage` posts to this endpoint after a user selects a file; ensure the payload matches `{ fileName, contentType, data }`.
- Vector schema is described in `backend/docs/Vector_DB.md`; use it when adjusting metadata.
