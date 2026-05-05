from typing import Any

from fastapi import APIRouter, HTTPException, Depends, status  # ADDED: Depends
from pydantic import BaseModel

# Local imports
from app.core.dependencies import get_current_user  # ADDED: auth dependency
from app.service.collection.collection_service import (
    CollectionNotFoundError,
    CollectionService,
    CollectionServiceError,
)
from app.service.rag.ingestion import ingest_upload_service
from app.service.rag.ingestion.ingest_job_service import (
    IngestJobService,
    IngestJobValidationError,
)

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


class IngestJobAcceptedResponse(BaseModel):
    """Response returned immediately after an upload job is accepted."""

    jobId: str
    status: str
    fileName: str
    collectionId: str


class IngestJobStatusResponse(BaseModel):
    """Current state for a background ingest job."""

    jobId: str
    status: str
    fileName: str
    collectionId: str
    collectionName: str | None = None
    result: IngestUploadResponse | None = None
    error: str | None = None
    submittedAt: str
    startedAt: str | None = None
    finishedAt: str | None = None


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


async def _resolve_user_and_collection(
    file: FileUpload,
    current_user: dict,
) -> tuple[str, dict[str, Any]]:
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

    return user_id, active_collection


async def _complete_ingest_upload(
    *,
    file: FileUpload,
    active_collection: dict[str, Any],
    user_id: str,
) -> IngestUploadResponse:
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
    await _upsert_chunks(parent_chunks, child_chunks, user_id)
    await CollectionService.reconcile_all_collection_file_counts(user_id)

    return IngestUploadResponse(
        status="ok",
        message="Upload completed successfully.",
        file_name=file.fileName,
        parent_chunks=len(parent_chunks),
        child_chunks=len(child_chunks),
        warnings=service_result["warnings"],
    )


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

    user_id, active_collection = await _resolve_user_and_collection(file, current_user)
    return await _complete_ingest_upload(
        file=file,
        active_collection=active_collection,
        user_id=user_id,
    )


@router.post(
    "/upload-jobs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestJobAcceptedResponse,
)
async def create_ingest_upload_job(
    file: FileUpload,
    current_user: dict = Depends(get_current_user),
):
    """Accept an upload and run ingestion in the background."""
    user_id, active_collection = await _resolve_user_and_collection(file, current_user)
    collection_id = str(active_collection.get("collection_id") or "").strip()
    collection_name = str(active_collection.get("name") or CollectionService.DEFAULT_COLLECTION_NAME)

    try:
        accepted = await IngestJobService.submit_ingest_job(
            user_id=user_id,
            file_name=file.fileName,
            content_type=file.contentType,
            data=file.data,
            collection_id=collection_id,
            collection_name=collection_name,
        )
    except IngestJobValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to queue ingest job: {exc}") from exc

    return IngestJobAcceptedResponse(
        jobId=accepted["jobId"],
        status=accepted["status"],
        fileName=accepted["fileName"],
        collectionId=accepted["collectionId"],
    )


@router.get("/upload-jobs/{job_id}", response_model=IngestJobStatusResponse)
async def get_ingest_upload_job_status(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return status/result/error for a background ingest upload."""
    user_id = str(current_user.get("sub") or "").strip() if isinstance(current_user, dict) else ""
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    job = await IngestJobService.get_ingest_job(job_id=job_id, user_id=user_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Ingest job '{job_id}' not found")
    return IngestJobStatusResponse(**job)


async def ingest_webhook(file: FileUpload):
    """Backward-compatible alias for ingest_upload (deprecated)."""
    return await ingest_upload(file)
