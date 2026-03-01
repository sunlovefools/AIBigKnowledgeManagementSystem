from pathlib import Path

from app.service.rag.ingestion import docling as _docling
from app.service.rag.ingestion.docling.config import (
    get_docling_backend_selection as _get_docling_backend_selection,
)


# Public constants/types remain importable from this module for compatibility.
DEFAULT_DOCLING_PAGE_CHUNK_SIZE = _docling.DEFAULT_DOCLING_PAGE_CHUNK_SIZE
DOCLING_IMAGE_PLACEHOLDER = _docling.DOCLING_IMAGE_PLACEHOLDER
DOCLING_IMAGE_CROP_FAILED_MARKER = _docling.DOCLING_IMAGE_CROP_FAILED_MARKER
ExtractedImageArtifact = _docling.ExtractedImageArtifact
DoclingChunkFailure = _docling.DoclingChunkFailure
DoclingParseStats = _docling.DoclingParseStats
DoclingStructuredBlock = _docling.DoclingStructuredBlock
DoclingParseResult = _docling.DoclingParseResult

def _get_docling_pdf_backend() -> str:
    """
    Select the Docling processing backend (beam/local). Defaults to beam.
    """
    return _get_docling_backend_selection()


def parse_pdf_with_docling_preview(
    pdf_bytes: bytes,
    file_name: str,
    artifact_root: Path | None = None,
    page_chunk_size: int = DEFAULT_DOCLING_PAGE_CHUNK_SIZE,
    file_id: str | None = None,
) -> DoclingParseResult:
    """
    Public Docling PDF preview entrypoint via the Docling facade.
    """
    return _docling.parse_pdf_with_docling_preview(
        pdf_bytes=pdf_bytes,
        file_name=file_name,
        artifact_root=artifact_root,
        page_chunk_size=page_chunk_size,
        file_id=file_id,
    )


def get_pdf_ingestion_strategy() -> str:
    """
    Determine the PDF ingestion strategy based on environment variable.
    """
    return _docling.get_pdf_ingestion_strategy()


__all__ = [
    "DEFAULT_DOCLING_PAGE_CHUNK_SIZE",
    "DOCLING_IMAGE_PLACEHOLDER",
    "DOCLING_IMAGE_CROP_FAILED_MARKER",
    "ExtractedImageArtifact",
    "DoclingChunkFailure",
    "DoclingParseStats",
    "DoclingStructuredBlock",
    "DoclingParseResult",
    "parse_pdf_with_docling_preview",
    "get_pdf_ingestion_strategy",
]
