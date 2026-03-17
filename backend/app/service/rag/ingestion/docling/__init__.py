"""
Docling ingestion facade.

Exports public constants/models and routes parsing through the unified pipeline.
"""

from __future__ import annotations

from pathlib import Path

from .config import (
    DEFAULT_DOCLING_PAGE_CHUNK_SIZE,
    DOCLING_IMAGE_CROP_FAILED_MARKER,
    DOCLING_IMAGE_PLACEHOLDER,
    get_docling_backend_selection,
    get_pdf_ingestion_strategy,
)
from .models import (
    DoclingChunkFailure,
    DoclingParseResult,
    DoclingParseStats,
    DoclingStructuredBlock,
    ExtractedImageArtifact,
)
from .pipeline import parse_pdf_with_docling as _parse_pdf_with_docling


def parse_pdf_with_docling(
    pdf_bytes: bytes,
    file_name: str,
    artifact_root: Path | None = None,
    page_chunk_size: int = DEFAULT_DOCLING_PAGE_CHUNK_SIZE,
    file_id: str | None = None,
) -> DoclingParseResult:
    """
    Public Docling PDF entrypoint that dispatches via selected backend.
    """

    backend = get_docling_backend_selection() # Either "beam" or "local"
    return _parse_pdf_with_docling(
        pdf_bytes=pdf_bytes,
        file_name=file_name,
        artifact_root=artifact_root,
        page_chunk_size=page_chunk_size,
        file_id=file_id,
        backend=backend,
    )


__all__ = [
    "DEFAULT_DOCLING_PAGE_CHUNK_SIZE",
    "DOCLING_IMAGE_PLACEHOLDER",
    "DOCLING_IMAGE_CROP_FAILED_MARKER",
    "ExtractedImageArtifact",
    "DoclingChunkFailure",
    "DoclingParseStats",
    "DoclingStructuredBlock",
    "DoclingParseResult",
    "parse_pdf_with_docling",
    "get_pdf_ingestion_strategy",
]
