import base64
import binascii
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Local imports
from app.core.id_utils import generate_uuid_v6
from app.service.rag.ingestion.chunk_polisher import polish_chunks
from app.service.rag.ingestion.chunker import split_parent_child_chunks
from app.service.rag.ingestion.docling_chunker import (
    split_parent_child_chunks_from_docling_blocks,
)
from app.service.rag.ingestion.docling_pdf_extractor import (
    get_pdf_ingestion_strategy,
    parse_pdf_with_docling_preview,
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
    # Fail fast with a client error when payload is not valid base64.
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="invalid base64 payload")


def _is_docling_pdf_strategy(file: FileUpload) -> bool:
    """
    Determine if the Docling PDF ingestion strategy should be used based on file type and environment configuration.
    """
    return (
        file.contentType == "application/pdf"
        and get_pdf_ingestion_strategy() == "docling"
    )


def _run_legacy_pipeline(
    file: FileUpload, file_bytes: bytes
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Legacy branch with simple text extraction and chunking using predefined chunk size, without Docling's visual metadata preservation.
    """
    try:
        # 1. Extrating the text from the PDF
        text = extract_text(file.contentType, file_bytes)
    except ValueError as error:
        raise HTTPException(status_code=415, detail=str(error))
    except Exception:
        raise HTTPException(status_code=500, detail="text extraction failed")

    canonical_text = canonicalize_markdown_text(text)

    # 2. Extracting the parent and child chunks from the extracted text
    parent_chunks_models, child_chunks_models = split_parent_child_chunks(
        canonical_text, file_name=file.fileName, parent_target_chars=1500, child_max_chars=600
    )

    parent_chunks_dicts = [chunk.model_dump() for chunk in parent_chunks_models]
    child_chunks_dicts = [chunk.model_dump() for chunk in child_chunks_models]

    # 3. Polishing child chunks making sure that it's good for vectorisation
    polished_child_chunks = polish_chunks(child_chunks_dicts)
    return parent_chunks_dicts, polished_child_chunks


def _run_docling_pipeline(
    file: FileUpload, file_bytes: bytes
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], str]:
    """
    Docling branch for chunking the PDF which is able to preserve the visual from the PDF
    and semantically chunk the PDF based on the layout structure from Docling.
    """
    file_id = generate_uuid_v6()
    try:
        # 1. Parse the PDDF with Docling, which produces structured blocks
        parse_result = parse_pdf_with_docling_preview(
            pdf_bytes=file_bytes,
            file_name=file.fileName,
            file_id=file_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"docling parsing failed: {exc}")

    if not parse_result.structured_blocks:
        raise HTTPException(
            status_code=422,
            detail="docling produced no structured blocks for this PDF",
        )

    try:
        # 2. Convert Docling structured blocks into Parent and Child chunks.
        parent_chunks_models, child_chunks_models = (
            split_parent_child_chunks_from_docling_blocks(
                blocks=parse_result.structured_blocks,
                file_name=file.fileName,
                artifact_dir=parse_result.artifact_dir,
                file_id=file_id,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"docling chunking failed: {exc}")

    parent_chunks_dicts = [chunk.model_dump() for chunk in parent_chunks_models]
    # Intentionally skip legacy chunk polishing to avoid changing Docling visual metadata.
    child_chunks_dicts = [chunk.model_dump() for chunk in child_chunks_models]
    warnings = list(parse_result.warnings)
    if parse_result.partial_failures:
        warnings.append(
            f"Docling reported {len(parse_result.partial_failures)} partial failure chunk(s)."
        )

    return (
        parent_chunks_dicts,
        child_chunks_dicts,
        warnings,
        parse_result.artifact_run_id,
    )


async def _upsert_chunks(
    parent_chunks: list[dict[str, Any]], child_chunks: list[dict[str, Any]]
) -> None:
    """
    Upsert the parent and child chunks into the vector database.
    """
    try:
        await upsert_documents(parent_chunks=parent_chunks, child_chunks=child_chunks)
    except Exception:
        raise HTTPException(status_code=500, detail="upsert to vector store failed")


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
