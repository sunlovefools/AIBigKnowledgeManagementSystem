from typing import Any

from fastapi import APIRouter, HTTPException, Depends  # ADDED: Depends
from pydantic import BaseModel

# Local imports
from app.core.dependencies import get_current_user  # ADDED: auth dependency
from app.service.collection.collection_service import (
    CollectionNotFoundError,
    CollectionService,
    CollectionServiceError,
)
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
    collectionId: str | None = None


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
    user_id: str,
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
    current_user: dict = Depends(get_current_user),
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

    try:
        active_collection = await CollectionService.resolve_active_collection(
            user_id=user_id,
            requested_collection_id=file.collectionId,
        )
    except CollectionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except CollectionServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

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
    parent_chunks, child_chunks = CollectionService.apply_collection_metadata_to_chunks(
        parent_chunks=parent_chunks,
        child_chunks=child_chunks,
        collection_id=str(active_collection.get("collection_id") or ""),
        collection_name=str(active_collection.get("name") or CollectionService.DEFAULT_COLLECTION_NAME),
    )
    # Ingestion 2: Insert the chunks into the vector database.
    await _upsert_chunks(parent_chunks, child_chunks, user_id)
    await CollectionService.reconcile_all_collection_file_counts(user_id)

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
