# Vector Ingestion Pipeline

This document describes how upload ingestion stores parent/child chunks and metadata in the vector layer.

## Endpoint

- `POST /ingest/upload`

`/ingest/upload` is the only public ingestion endpoint.

## Strategy Selection

For `application/pdf`, strategy is controlled by `INGEST_PDF_EXTRACTOR`:

- `legacy`: legacy extraction + legacy chunking + child chunk polishing
- `docling`: Docling parse + Docling structured-block chunking

Non-PDF files always use the legacy path.

## High-Level Flow

```text
Frontend Upload
  -> FastAPI /ingest/upload
  -> Base64 decode + request validation
  -> Strategy branch
     - legacy: extract_text -> split_parent_child_chunks -> polish_chunks
     - docling: parse_pdf_with_docling_preview -> split_parent_child_chunks_from_docling_blocks
  -> upsert_documents(parent_chunks, child_chunks)
  -> Astra parent store + vector store
```

## Stored Metadata

The vector upsert path stores child documents with metadata such as:

- `file_metadata`
- `child_chunk_metadata`
- `content_flags`
- `artifact_refs`

Docling uploads preserve richer visual metadata (for image/table-derived chunks) in `content_flags` and `artifact_refs`.

## Error Behavior

- Invalid base64 payload: `400`
- Unsupported legacy extraction type: `415`
- Docling selected but no structured blocks: `422`
- Upsert or parse/chunk failures: `500`

## Related Files

- `backend/app/api/router_ingest.py`
- `backend/app/vectordb/vectordb.py`
- `backend/app/service/rag/ingestion/chunker.py`
- `backend/app/service/rag/ingestion/docling_chunker.py`
