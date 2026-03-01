# API Endpoint Test Helpers

This folder contains manual scripts for testing backend API endpoints that accept file uploads.

## Upload Route Test (`/ingest/upload`)

Start the backend first:

```bash
cd backend
uvicorn app.main:app --reload
```

Send a PDF to the unified upload endpoint:

```bash
python backend/tests/api_endpoint/send_file_to_endpoint.py --file "C:\path\to\sample.pdf"
```

## Useful Variants

Send to the upload endpoint:

```bash
python backend/tests/api_endpoint/send_file_to_endpoint.py ^
  --file "C:\path\to\sample.pdf" ^
  --endpoint "http://127.0.0.1:8000/ingest/upload"
```

Override MIME type manually:

```bash
python backend/tests/api_endpoint/send_file_to_endpoint.py ^
  --file "C:\path\to\file.bin" ^
  --content-type "application/pdf"
```

Save response JSON to disk:

```bash
python backend/tests/api_endpoint/send_file_to_endpoint.py ^
  --file "C:\path\to\sample.pdf" ^
  --save-response "backend\tests\api_endpoint\last_response.json"
```
