from typing import Any

from fastapi import APIRouter, HTTPException, Depends  # ADDED: Depends
from pydantic import BaseModel

# Local imports
from app.core.dependencies import get_current_user  # ADDED: auth dependency
from app.service.rag.ingestion import ingest_upload_service

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


class IngestUploadResponse(BaseModel):
    """Response model for the unified upload endpoint."""

    status: str
    message: str 
    file_name: str
    parent_chunks: int
    child_chunks: int
    warnings: list[str]


async def _upsert_chunks(
    parent_chunks: list[dict[str, Any]],
    child_chunks: list[dict[str, Any]],
    user_id: str,  # ADDED: passed down to upsert_documents so every chunk is tagged with its owner
) -> None:
    """
    Upsert parent/child chunks into vector storage.
    """
    try:
        await ingest_upload_service.upsert_chunks(
            parent_chunks=parent_chunks,
            child_chunks=child_chunks,
            user_id=user_id,
        )
    except ingest_upload_service.UpsertChunksFailedError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# --- Endpoint ---
@router.get("/health")
def ingest_health():
    """
    Health check endpoint for ingestion module.
    """

    return {"ingestion": "ok"}


@router.post("/upload", response_model=IngestUploadResponse)
async def ingest_upload(
    file: FileUpload,
    current_user: dict = Depends(get_current_user),  # ADDED: locks route + provides user_id
):
    """
    Unified ingestion endpoint for all document types.
    This endpoint routes doesn't include modification, it purely focuses on new ingestions.
    
    Strategy routing:
    - PDF: Docling branch when INGEST_PDF_EXTRACTOR=docling
    - PowerPoint/Excel: Docling branch
    - Other formats: Legacy branch (PyMuPDF, python-docx, plain text)
    """

    user_id = str(current_user.get("sub") or "").strip() if isinstance(current_user, dict) else ""
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Ingestion 1: Decode the file bytes from base64
    try:
        service_result = await ingest_upload_service.run_ingest_upload(
            file_name=file.fileName,
            content_type=file.contentType,
            data=file.data,
        )
    except ingest_upload_service.InvalidBase64PayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ingest_upload_service.UnsupportedIngestContentTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc))
    except ingest_upload_service.LegacyTextExtractionFailedError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except ingest_upload_service.DoclingParsingFailedError as exc:
        raise HTTPException(status_code=500, detail=f"docling parsing failed: {exc}")
    except ingest_upload_service.DoclingNoStructuredBlocksError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ingest_upload_service.DoclingChunkingFailedError as exc:
        raise HTTPException(status_code=500, detail=f"docling chunking failed: {exc}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ingest upload failed: {exc}") from exc

    parent_chunks = service_result["parent_chunks"]
    child_chunks = service_result["child_chunks"]
    # Ingestion 2: Insert the chunks into the vector database.
    await _upsert_chunks(parent_chunks, child_chunks, user_id)

    # Ingestion 3: Return a unified response back to the frontend
    return IngestUploadResponse(
        status="ok",
        message="Upload completed successfully.",
        file_name=file.fileName,
        parent_chunks=len(parent_chunks),
        child_chunks=len(child_chunks),
        warnings=service_result["warnings"],
    )


async def ingest_webhook(file: FileUpload):
    """Backward-compatible alias for ingest_upload (deprecated)."""
    return await ingest_upload(file)
