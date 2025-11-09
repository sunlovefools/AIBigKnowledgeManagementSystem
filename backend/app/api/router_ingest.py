from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime
from pathlib import Path

from app.service.text_extractor import extract_text
from app.service.chunker import split_into_chunks

router = APIRouter(prefix="/ingest", tags=["Ingestion"])

# --- Constants and paths ---
MAX_SIZE = 50 * 1024 * 1024  # 50 MB
# file in: backend/app/api/router_ingest.py
# parents[0]=.../api, [1]=.../app, [2]=.../backend
BASE_DIR = Path(__file__).resolve().parents[2]    # .../backend
LOCAL_ROOT = BASE_DIR / "_local_uploads"          # .../backend/_local_uploads

# --- The model of the event that MinIO webhook typically sends ---
class IngestEvent(BaseModel):
    bucket: str
    object_key: str           # e.g.: "user-123/demo.txt"
    filename: str
    content_type: str         # "text/plain" | "application/pdf" | "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    size_bytes: int
    user_id: str
    upload_ts: datetime

# --- Simple health for this module ---
@router.get("/health")
def ingest_health():
    return {"ingestion": "ok"}

# --- Main endpoint: receive event, extract text, cut into chunks ---
@router.post("/webhook")
def ingest_webhook(
    ev: IngestEvent,
    preview: int = Query(0, description="Set 1 to include chunk previews"),
    chunk_size: int = Query(4000, ge=200, le=20000, description="Max chars per chunk"),
    preview_limit: int = Query(5, ge=1, le=100, description="How many chunks to preview")
):
    # basic metadata checks
    if ev.size_bytes <= 0:
        raise HTTPException(status_code=400, detail="size_bytes must be > 0")
    if ev.size_bytes > MAX_SIZE:
        raise HTTPException(status_code=413, detail="file too large")

    # Here program reads uploaded files (instead of MinIO at early stage)
    local_path = LOCAL_ROOT / ev.object_key  # e.g.: backend/_local_uploads/user-123/demo.txt
    if not local_path.exists():
        raise HTTPException(status_code=404, detail=f"local mock file not found: {local_path}")

    data = local_path.read_bytes()

    # text extraction
    try:
        text = extract_text(ev.content_type, data)
    except ValueError as e:
        # if unsupported type
        raise HTTPException(status_code=415, detail=str(e))
    except Exception:
        # if unexpected error
        raise HTTPException(status_code=500, detail="text extraction failed")

    # chunking
    chunks = split_into_chunks(text, max_chars=chunk_size)

    # output
    resp = {
        "status": "chunked",
        "bucket": ev.bucket,
        "object_key": ev.object_key,
        "filename": ev.filename,
        "chars": len(text),
        "chunks_count": len(chunks),
    }

    # if requested - preview of the first chunks
    if preview:
        resp["chunks_preview"] = [
            {"index": c["index"], "len": len(c["text"]), "preview": c["text"]}
            for c in chunks[:preview_limit]
        ]

    return resp
