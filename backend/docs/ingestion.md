# Ingestion Pipeline (`/ingest/upload`)

Detailed chunking behavior (section -> parent -> child rules and edge cases) is documented in:
- `chunk_construction_rules.md`

## Overview
The ingestion API accepts a base64-encoded file upload and stores parent/child chunks in the vector pipeline.
There is one public ingestion endpoint:

- `POST /ingest/upload`

The endpoint chooses the PDF processing strategy using `INGEST_PDF_EXTRACTOR`:

- `legacy` (default): legacy extraction + legacy chunking + chunk polishing
- `docling`: Docling parse + Docling structured-block chunking (metadata-preserving)

Non-PDF files always use the legacy path.

## Request Contract

### Endpoint
`POST /ingest/upload`

### JSON body
```json
{
  "fileName": "example.pdf",
  "contentType": "application/pdf",
  "data": "<base64-file-content>"
}
```

## Response Contract

Success response:
```json
{
  "status": "ok",
  "message": "Upload completed successfully.",
  "file_name": "example.pdf",
  "strategy": "docling",
  "parent_chunks": 5,
  "child_chunks": 18,
  "warnings": []
}
```

## Processing Behavior

### Legacy branch
Used when:
- file is not a PDF, or
- file is a PDF and `INGEST_PDF_EXTRACTOR=legacy`

Flow:
1. Decode base64 payload.
2. Extract text (`text_extractor.extract_text`).
3. Split into parent/child chunks (`split_parent_child_chunks`).
4. Polish child chunks (`polish_chunks`).
5. Upsert to vector stores.

### Docling branch
Used when:
- file is a PDF and `INGEST_PDF_EXTRACTOR=docling`

Flow:
1. Decode base64 payload.
2. Parse PDF with Docling (`parse_pdf_with_docling`).
3. Require non-empty `structured_blocks`.
4. Chunk via Docling chunker (`split_parent_child_chunks_from_docling_blocks`).
5. Upsert to vector stores without legacy polish step.
6. When semantic-table ingestion is enabled, classify each non-image markdown table and:
   - `layout`: flatten to key-value bullets and keep normal chunking.
   - `matrix` / `entity_list`: generate semantic child/parent table chunks and merge with standard chunks.

Docling branch preserves richer metadata for visual content, including:
- `content_flags`
- `artifact_refs`

## Error Handling

- `400`: invalid base64 payload
- `415`: unsupported file/content type in legacy extractor
- `422`: Docling selected but no structured blocks produced
- `500`: Docling parse/chunking failures or vector upsert failures

## Environment Variables

- `INGEST_PDF_EXTRACTOR=legacy|docling`
- `DOCLING_BACKEND_SELECTION=beam|local`
- `DOCLING_ARTIFACTS_ENABLED=true|false`
- `TABLE_SEMANTIC_INGESTION_ENABLED=true|false` (defaults to `true`)
- `TABLE_SEMANTIC_LLM_URL=<gemini-generateContent-base-url>` (defaults to Gemini, e.g. `https://generativelanguage.googleapis.com/v1beta`)
- `TABLE_SEMANTIC_LLM_API_KEY=<gemini-api-key>` (falls back to `TABLE_IMAGE_VLM_API_KEY`, `GOOGLE_GEMINI_API_KEY`, or `GEMINI_API_KEY`)
- `TABLE_SEMANTIC_CLASSIFIER_MODEL=<model-name>`
- `TABLE_SEMANTIC_GLOBAL_MODEL=<model-name>`
- `TABLE_SEMANTIC_ROW_MODEL=<model-name>`
- `TABLE_SEMANTIC_TIMEOUT_S=<seconds>`
- `TABLE_SEMANTIC_MAX_SAMPLE_ROWS=<int>`

## Local Test (curl)

```bash
curl -X POST http://127.0.0.1:8000/ingest/upload ^
  -H "Content-Type: application/json" ^
  -d "{\"fileName\":\"demo.pdf\",\"contentType\":\"application/pdf\",\"data\":\"<base64>\"}"
```

## Notes

- `/ingest/webhook` and `/ingest/webhook/preview` are removed.
- Frontend upload calls should target `/ingest/upload`.
