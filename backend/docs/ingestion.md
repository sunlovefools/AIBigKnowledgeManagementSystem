# Ingestion Pipeline (`/ingest/upload`)

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
2. Parse PDF with Docling (`parse_pdf_with_docling_preview`).
3. Require non-empty `structured_blocks`.
4. Chunk via Docling chunker (`split_parent_child_chunks_from_docling_blocks`).
5. Upsert to vector stores without legacy polish step.

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

## Local Test (curl)

```bash
curl -X POST http://127.0.0.1:8000/ingest/upload ^
  -H "Content-Type: application/json" ^
  -d "{\"fileName\":\"demo.pdf\",\"contentType\":\"application/pdf\",\"data\":\"<base64>\"}"
```

## Notes

- `/ingest/webhook` and `/ingest/webhook/preview` are removed.
- Frontend upload calls should target `/ingest/upload`.
