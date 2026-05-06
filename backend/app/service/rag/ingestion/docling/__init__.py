"""
Docling ingestion facade.

Routes parsing through the unified pipeline.
"""

from __future__ import annotations

from pathlib import Path

from .config import (
    DEFAULT_DOCLING_PAGE_CHUNK_SIZE,
    get_docling_backend_selection,
    get_pdf_ingestion_strategy,
)
from .models import DoclingParseResult
from .pipeline import parse_pdf_with_docling as _parse_pdf_with_docling


def parse_pdf_with_docling(
    pdf_bytes: bytes,
    file_name: str,
    page_chunk_size: int = DEFAULT_DOCLING_PAGE_CHUNK_SIZE,
    file_id: str | None = None,
    run_id: str | None = None,
    artifact_dir: Path | None = None,
    markdown_path: Path | None = None,
) -> DoclingParseResult:
    """
    Public Docling PDF entrypoint that dispatches via selected backend.
    Currently only called by only ingest_upload_service.run_docling_pdf_pipeline
    """

    backend = get_docling_backend_selection() # Either "beam" or "local"
    return _parse_pdf_with_docling(
        pdf_bytes=pdf_bytes,
        file_name=file_name,
        page_chunk_size=page_chunk_size,
        file_id=file_id,
        run_id=run_id,
        artifact_dir=artifact_dir,
        markdown_path=markdown_path,
        backend=backend,
    )


__all__ = [
    "parse_pdf_with_docling",
    "get_pdf_ingestion_strategy",
]
