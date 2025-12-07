from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime
from pathlib import Path

from app.service.rag.ingestion.text_extractor import extract_text
from app.service.rag.ingestion.chunker import split_parent_child_chunks
from app.service.rag.ingestion.chunk_polisher import polish_chunks
from app.vectordb.vectordb import upsert_documents

# For decoding base64 file data
import base64

# Setup the API router
router = APIRouter()

# --- Constants and paths ---
MAX_SIZE = 50 * 1024 * 1024  # 50 MB
# file in: backend/app/api/router_ingest.py
# parents[0]=.../api, [1]=.../app, [2]=.../backend
BASE_DIR = Path(__file__).resolve().parents[2]    # .../backend
LOCAL_ROOT = BASE_DIR / "_local_uploads"          # .../backend/_local_uploads

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
@router.post("/webhook")
async def ingest_webhook(file: FileUpload):
    
    # decode the data from base64 into bytes
    file_bytes = base64.b64decode(file.data)

    # 1. Extract text from the file bytes
    try:
        text = extract_text(file.contentType, file_bytes)
    except ValueError as error:
        raise HTTPException(status_code=415, detail=str(error))
    except Exception:
        raise HTTPException(status_code=500, detail="text extraction failed")

    print("Successfully extracted text")
    # 2. Parent-Child Splitting
    # Returns lists of Pydantic models: [ParentChunkModel], [ChildChunkModel] (Refer to chunker.py)
    parent_chunks_models, child_chunks_models = split_parent_child_chunks(
        text, 
        file_name=file.fileName,
        parent_max_chars=1500,
        child_max_chars=600    
    )

    # 3. Preparation for Polishing: Convert Child Models to raw dictionaries
    # The polisher expects a List[Dict[str, Any]]. We use .model_dump() for conversion.
    child_chunks_dicts = [chunk.model_dump(by_alias=False) for chunk in child_chunks_models]
    
    # 4. Polishing: Applied only to the embeddable child chunks' text
    polished_child_chunks = polish_chunks(child_chunks_dicts)

    parent_chunks_dicts = [chunk.model_dump(by_alias=True) for chunk in parent_chunks_models]
    try:
        # 5. Upsert both Parent and Child chunks into their respective stores
        await upsert_documents(
            parent_chunks=parent_chunks_dicts,
            child_chunks=polished_child_chunks
        )
        print("✅ Upserted all chunks into vector store.")
    except Exception:
        raise HTTPException(status_code=500, detail="upsert to vector store failed")
