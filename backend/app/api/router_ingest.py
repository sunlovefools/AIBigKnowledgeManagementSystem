import base64
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Local imports
from app.service.rag.ingestion.text_extractor import extract_text
from app.service.rag.ingestion.chunker import split_parent_child_chunks
from app.service.rag.ingestion.chunk_polisher import polish_chunks
from app.vectordb.vectordb import upsert_documents

# Setup the API router
router = APIRouter()

# --- Data Models ---
class FileUpload(BaseModel):
    """
    Model for file upload.
    Expects base64-encoded file data.
    """
    fileName: str
    contentType: str
    data: str

# --- Endpoint ---
@router.get("/health")
def ingest_health():
    """
    Health check endpoint for ingestion module.
    """
    return {"ingestion": "ok"}

@router.post("/webhook")
async def ingest_webhook(file: FileUpload):
    """
    Main ingestion endpoint.
    
    Functionality:
    1. Extract text from the uploaded file.
    2. Split the text into Parent and Child chunks.
    3. Polish the Child chunks for better embedding quality.
    4. Upsert both Parent and Child chunks into their respective stores.
    """
    
    # decode the data from base64 into bytes
    file_bytes = base64.b64decode(file.data)

    # 1. Extract text from the file bytes
    try:
        text = extract_text(file.contentType, file_bytes)
    except ValueError as error:
        raise HTTPException(status_code=415, detail=str(error))
    except Exception:
        raise HTTPException(status_code=500, detail="text extraction failed")

    print(f"✅ Successfully extracted text from {file.fileName}")

    # 2. Parent-Child Splitting
    # Returns lists of Pydantic models: [ParentChunkModel], [ChildChunkModel] (Refer to chunker.py)
    parent_chunks_models, child_chunks_models = split_parent_child_chunks(
        text, 
        file_name=file.fileName,
        parent_target_chars=1500,
        child_max_chars=600    
    )

    # 3. Preparation for Polishing: Convert Child Models to raw dictionaries
    # The polisher expects a List[Dict[str, Any]]. We use .model_dump() for conversion.
    child_chunks_dicts = [chunk.model_dump() for chunk in child_chunks_models]
    
    # 4. Polishing: Applied only to the embeddable child chunks' text
    polished_child_chunks = polish_chunks(child_chunks_dicts)

    parent_chunks_dicts = [chunk.model_dump() for chunk in parent_chunks_models]
    
    # 5. Upsert both Parent and Child chunks into their respective stores
    try:
        await upsert_documents(
            parent_chunks=parent_chunks_dicts,
            child_chunks=polished_child_chunks
        )
        print("✅ Upserted all chunks into vector store.")
    except Exception:
        raise HTTPException(status_code=500, detail="upsert to vector store failed")
