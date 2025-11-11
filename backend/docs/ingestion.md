# Ingestion Pipeline - Text Extraction and Chunking

## Overview
The ingestion component of our project is responsible for receiving uploaded documents, extracting text from them, and splitting this text into manageable chunks that can later be embedded and stored in a vector database.  
This service forms the **first stage** of the knowledge ingestion pipeline.

---

## Architecture Flow
1. **Upload** - A user uploads a file (PDF, DOCX, or TXT) to the MinIO storage bucket.  
2. **Webhook Trigger** - MinIO sends a webhook notification to the backend endpoint `/ingest/webhook`.  
3. **Validation** - The backend checks:
   - File size (`<= 50 MB`)
   - Supported content type
   - Existence of the object in storage
4. **Text Extraction** - 
   - PDF → [PyMuPDF](https://pymupdf.readthedocs.io/) (`fitz`)
   - DOCX → [python-docx](https://python-docx.readthedocs.io/)
   - TXT → raw UTF-8 reading
5. **Chunking** — The extracted text is divided into logical fragments (chunks) of configurable size.  
   - Default: `4000` characters per chunk  
   - Configurable via `chunk_size` query parameter
6. **Response** — The API returns metadata including total character count, number of chunks, and an optional preview.

---

## API Endpoint

### `POST /ingest/webhook`
#### Request Body
```json
{
  "bucket": "uploads",
  "object_key": "user-123/TEST.pdf",
  "filename": "TEST.pdf",
  "content_type": "application/pdf",
  "size_bytes": 123456, 
  "user_id": "user-123",
  "upload_ts": "2025-11-09T11:00:00Z"
}
```


# Ingestion: Local Test Guide

#### All commands are executed from the backend/ folder

#### 0) Preparing the environment
```json
python3 -m venv venv

source venv/bin/activate

pip install -r requirements.txt
```
#### 1) Minimal .env (to prevent the backend from crashing due to auth)
If you disabled `router_auth` import in `app/main.py`,  
you can skip this step. Otherwise, create a dummy `.env` file to avoid startup errors:
```json
cat > .env <<'ENV'
ASTRA_DB_URL=https://example-astra-url
ASTRA_DB_TOKEN=example-astra-token
ENV
```

(Note: .env file is required only to satisfy the existing authentication module imports. The ingestion service itself does not use Astra DB.)

#### 2) Local storage (temporary solution without MinIO)
```json
mkdir -p _local_uploads/user-123
echo "Hello from local TXT. Line 2. Line 3." > _local_uploads/user-123/demo.txt
#### Place here if necessary: TEST.pdf, TEST3.docx, etc.
```
#### 3) Starting the server (leave the terminal open)
```json
uvicorn app.main:app --reload --port 8000
```
#### 4) Health-check of the ingestion module (in the new terminal)
```json
curl http://127.0.0.1:8000/ingest/health
##### Expect: {"ingestion":"ok"}
```
#### 5) How to find out the file size (in bytes) - substitute the desired path
```json
stat -c %s _local_uploads/user-123/FILE.EXT
```
#### 6) Tests

#### --- 6A. TXT ---
#### Query body: 
```json
cat > /tmp/ing-txt.json <<'JSON'
{
  "bucket": "uploads",
  "object_key": "user-123/demo.txt",
  "filename": "demo.txt",
  "content_type": "text/plain",
  "size_bytes": SIZE_HERE,
  "user_id": "user-123",
  "upload_ts": "2025-11-09T11:00:00Z"
}
JSON
```
#### Sending a request (with preview and reduced chunk size):
```json
curl -s 'http://127.0.0.1:8000/ingest/webhook?preview=1&chunk_size=500&preview_limit=10' \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/ing-txt.json | python3 -m json.tool
```

#### --- 6B. PDF ---
#### File should be in: _local_uploads/user-123/TEST2.pdf
#### Add real file size (in bytes)
```json
cat > /tmp/ing-pdf.json <<'JSON'
{
  "bucket": "uploads",
  "object_key": "user-123/TEST2.pdf",
  "filename": "TEST2.pdf",
  "content_type": "application/pdf",
  "size_bytes": SIZE_HERE,
  "user_id": "user-123",
  "upload_ts": "2025-11-09T11:00:00Z"
}
JSON
sed -i "s/SIZE_HERE/$(stat -c %s _local_uploads/user-123/TEST2.pdf)/" /tmp/ing-pdf.json
```
#### Sending (see the first 5 chunks, increase the number if necessary):
```json
curl -s 'http://127.0.0.1:8000/ingest/webhook?preview=1&chunk_size=500&preview_limit=200' \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/ing-pdf.json | python3 -m json.tool
```

#### --- 6C. DOCX ---
#### File should be in: ```_local_uploads/user-123/TEST3.docx```
```json
cat > /tmp/ing-docx.json <<'JSON'
{
  "bucket": "uploads",
  "object_key": "user-123/TEST3.docx",
  "filename": "TEST3.docx",
  "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "size_bytes": SIZE_HERE,
  "user_id": "user-123",
  "upload_ts": "2025-11-09T11:00:00Z"
}
JSON
sed -i "s/SIZE_HERE/$(stat -c %s _local_uploads/user-123/TEST3.docx)/" /tmp/ing-docx.json

curl -s 'http://127.0.0.1:8000/ingest/webhook?preview=1&chunk_size=500&preview_limit=50' \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/ing-docx.json | python3 -m json.tool
```

#### 7) Common errors
#### ```404 Local mock file not found``` -> check the path and object_key in _local_uploads/<object_key>
#### ```415 Unsupported content_type``` -> check the content_type (pdf/docx/txt)
####  ```The server crashes due to auth/Astra``` -> check .env or temporarily comment out router_auth in app/main.py
