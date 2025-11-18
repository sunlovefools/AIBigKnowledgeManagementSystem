from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime
from pathlib import Path

from app.service.text_extractor import extract_text
from app.service.chunker import split_into_chunks
from app.service.chunk_polisher import polish_chunks
from app.service.embedder import embed_text
from app.service.vector_store import upsert_chunk

# For decoding base64 file data
import base64
import aiohttp



# Setup the API router
router = APIRouter()

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

# --- The model for file upload (used for when there is no real webhook) ---
class FileUpload(BaseModel):
    fileName: str
    contentType: str
    data: str

# --- Simple health for this module ---
@router.get("/health")
def ingest_health():
    return {"ingestion": "ok"}

# --- Main endpoint: receive event, extract text, cut into chunks ---
## Will still uses /webhook route eventhough there is no real webhook from MinIO at this stage
@router.post("/webhook")
async def ingest_webhook(file: FileUpload):
#     ev: IngestEvent,
#     preview: int = Query(0, description="Set 1 to include chunk previews"),
#     chunk_size: int = Query(4000, ge=200, le=20000, description="Max chars per chunk"),
#     preview_limit: int = Query(5, ge=1, le=100, description="How many chunks to preview")
# ):
#     # basic metadata checks
#     if ev.size_bytes <= 0:
#         raise HTTPException(status_code=400, detail="size_bytes must be > 0")
#     if ev.size_bytes > MAX_SIZE:
#         raise HTTPException(status_code=413, detail="file too large")

#     # Here program reads uploaded files (instead of MinIO at early stage)
#     local_path = LOCAL_ROOT / ev.object_key  # e.g.: backend/_local_uploads/user-123/demo.txt
#     if not local_path.exists():
#         raise HTTPException(status_code=404, detail=f"local mock file not found: {local_path}")

#     data = local_path.read_bytes()
    # decode the data from base64 into bytes
    file_bytes = base64.b64decode(file.data)

    # text extraction
    try:
        # text is a huge string of all extracted text
        text = extract_text(file.contentType, file_bytes)
    except ValueError as error:
        # if unsupported type
        raise HTTPException(status_code=415, detail=str(error))
    except Exception:
        # if unexpected error
        raise HTTPException(status_code=500, detail="text extraction failed")

    # chunking
    chunks = split_into_chunks(text, 1000)
    
    # Polishing chunks (Remove unwanted characters such as '\n')
    polished_chunks = polish_chunks(chunks)

    # prepare payload for embedding
    documents_payload = {
        "input": [chunk["text"] for chunk in polished_chunks]
    }

    # Call embedding API asynchronously
    try:
        async with aiohttp.ClientSession() as session:
            vectors = await embed_text(session, documents_payload)
            # Responses format from embed_text():
            # {"embedding": [[float, ...], ...] }
            print(f"vectors: {vectors}\n\n\n")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding request failed: {e}")

    # Write the vectors into a txt file for debugging
    with open("vectors_debug.txt", "w", encoding="utf-8") as f:
        f.write(str(vectors))
    
    # Extract embeddings list
    embeddings = vectors.get("embedding")

    # Check if embeddings are returned properly from beam, if not raise error
    if not embeddings:
        raise HTTPException(
            status_code=500,
            detail=f"Embedding server did not return valid embeddings. Got: {vectors}"
        )

    # Attach embeddings back to their chunks
    for index, vector in enumerate(embeddings):
        polished_chunks[index]["embedding"] = vector
        polished_chunks[index]["file_name"] = file.fileName

    # Print the dimension of the embeddings for debugging
    if len(embeddings) > 0:
        print(f"Embedding dimension: {len(embeddings[0])}")
    else:
        print("No embeddings returned.")
        
    # Polished chunks now format:
    # [
    #   {
    #       "index": 0,
    #       "text": "The text content of the chunk...",
    #       "embedding": [0.123, 0.456, ...],
    #       "file_name": "example.txt"
    #   },
    # ]

    # Write the polished chunks with embeddings into a txt file for debugging
    with open("polished_chunks_debug.txt", "w", encoding="utf-8") as f:
        f.write(str(polished_chunks))

    # --- Save to vector DB with basic error logging ---
    for chunk in polished_chunks:
        try:
            print(
                f"Upserting chunk {chunk['index']} "
                f"(len={len(chunk['embedding'])}) for file {chunk['file_name']}"
            )
            upsert_chunk(
                content=chunk["text"],
                document_name=chunk["file_name"],
                page_number=0,               # TODO: add numbers (if needed)
                chunk_number=chunk["index"],
                uploaded_by="demo-user",     # TODO: change later
                embedding=chunk["embedding"],
            )
        except Exception as e:
            print(f"❌ Failed to upsert chunk {chunk['index']}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Vector DB upsert failed: {e}"
            )


    # # output
    # resp = {
    #     "status": "chunked",
    #     "bucket": ev.bucket,
    #     "object_key": ev.object_key,
    #     "filename": ev.filename,
    #     "chars": len(text),
    #     "chunks_count": len(chunks),
    # }

    # # if requested - preview of the first chunks
    # if preview:
    #     resp["chunks_preview"] = [
    #         {"index": c["index"], "len": len(c["text"]), "preview": c["text"]}
    #         for c in chunks[:preview_limit]
    #     ]

    # return resp
