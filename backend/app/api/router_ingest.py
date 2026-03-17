from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Local imports
from app.core.id_utils import generate_uuid_v6
from app.service.rag.ingestion import ingest_upload_service
from app.service.rag.ingestion.chunk_polisher import polish_chunks
from app.service.rag.ingestion.chunker import split_parent_child_chunks
from app.service.rag.ingestion.docling_chunker import (
    split_parent_child_chunks_from_docling_blocks,
)
from app.service.rag.ingestion.docling import (
    get_pdf_ingestion_strategy,
    parse_pdf_with_docling,
)
from app.service.rag.ingestion.markdown_canonicalizer import (
    canonicalize_chunk_payloads_for_storage,
    canonicalize_markdown_text,
)
from app.service.rag.ingestion.text_extractor import extract_text
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


class IngestUploadResponse(BaseModel):
    """Response model for the unified upload endpoint."""

    status: str
    message: str
    file_name: str
    strategy: str
    parent_chunks: int
    child_chunks: int
    warnings: list[str]


def _decode_base64(data: str) -> bytes:
    """
    Decode a base64-encoded string into bytes, with error handling for invalid input.
    """
    try:
        return ingest_upload_service.decode_base64(data)
    except ingest_upload_service.InvalidBase64PayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _is_docling_pdf_strategy(file: FileUpload) -> bool:
    """
    Determine if the Docling PDF ingestion strategy should be used based on file type and environment configuration.
    """
    return ingest_upload_service.is_docling_pdf_strategy(
        content_type=file.contentType,
        pdf_ingestion_strategy=get_pdf_ingestion_strategy(),
    )


def _run_legacy_pipeline(
    file: FileUpload, file_bytes: bytes
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        return ingest_upload_service.run_legacy_pipeline(
            file_name=file.fileName,
            content_type=file.contentType,
            file_bytes=file_bytes,
            extract_text_fn=extract_text,
            canonicalize_markdown_text_fn=canonicalize_markdown_text,
            split_parent_child_chunks_fn=split_parent_child_chunks,
            polish_chunks_fn=polish_chunks,
        )
    except ingest_upload_service.UnsupportedIngestContentTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc))
    except ingest_upload_service.LegacyTextExtractionFailedError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def _run_docling_pipeline(
    file: FileUpload, file_bytes: bytes
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], str]:
    try:
        return ingest_upload_service.run_docling_pipeline(
            file_name=file.fileName,
            file_bytes=file_bytes,
            generate_uuid_fn=generate_uuid_v6,
            parse_pdf_with_docling_fn=parse_pdf_with_docling,
            split_parent_child_chunks_from_docling_blocks_fn=split_parent_child_chunks_from_docling_blocks,
        )
    except ingest_upload_service.DoclingParsingFailedError as exc:
        raise HTTPException(status_code=500, detail=f"docling parsing failed: {exc}")
    except ingest_upload_service.DoclingNoStructuredBlocksError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ingest_upload_service.DoclingChunkingFailedError as exc:
        raise HTTPException(status_code=500, detail=f"docling chunking failed: {exc}")


async def _upsert_chunks(
    parent_chunks: list[dict[str, Any]], child_chunks: list[dict[str, Any]]
) -> None:
    try:
        await ingest_upload_service.upsert_chunks(
            parent_chunks=parent_chunks,
            child_chunks=child_chunks,
            upsert_documents_fn=upsert_documents,
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
async def ingest_upload(file: FileUpload):
    """
    Unified ingestion endpoint.
    Strategy:
    - Docling branch only for PDFs when INGEST_PDF_EXTRACTOR=docling.
    - Legacy branch for everything else.
    """

    file_bytes = _decode_base64(file.data)
    strategy = "legacy"
    warnings: list[str] = []
    run_id: str | None = None

    # Run the appropriate ingestion pipeline based on file type and environment configuration.
    if _is_docling_pdf_strategy(file):
        strategy = "docling"
        (
            parent_chunks_dicts,
            child_chunks_dicts,
            warnings,
            run_id,
        ) = _run_docling_pipeline(file, file_bytes)
    else:
        parent_chunks_dicts, child_chunks_dicts = _run_legacy_pipeline(file, file_bytes)

    # Canonicalise each of the chunks to ensure consistent format
    parent_chunks_dicts, child_chunks_dicts = canonicalize_chunk_payloads_for_storage(
        parent_chunks_dicts,
        child_chunks_dicts,
    )

    # Insert the chunks into the vector database.
    await _upsert_chunks(parent_chunks_dicts, child_chunks_dicts)

    print(
        "[ingest-upload] file=%s strategy=%s parent_chunks=%s child_chunks=%s run_id=%s"
        % (
            file.fileName,
            strategy,
            len(parent_chunks_dicts),
            len(child_chunks_dicts),
            run_id or "n/a",
        )
    )

    return IngestUploadResponse(
        status="ok",
        message="Upload completed successfully.",
        file_name=file.fileName,
        strategy=strategy,
        parent_chunks=len(parent_chunks_dicts),
        child_chunks=len(child_chunks_dicts),
        warnings=warnings,
    )
