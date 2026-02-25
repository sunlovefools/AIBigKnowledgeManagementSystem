import os
from pathlib import Path

from app.service.rag.ingestion import docling_pdf_extractor_beam as _beam


# Public constants/types remain importable from this module for compatibility.
DEFAULT_DOCLING_PAGE_CHUNK_SIZE = _beam.DEFAULT_DOCLING_PAGE_CHUNK_SIZE
DOCLING_IMAGE_PLACEHOLDER = _beam.DOCLING_IMAGE_PLACEHOLDER
DOCLING_IMAGE_CROP_FAILED_MARKER = _beam.DOCLING_IMAGE_CROP_FAILED_MARKER

ExtractedImageArtifact = _beam.ExtractedImageArtifact
DoclingChunkFailure = _beam.DoclingChunkFailure
DoclingParseStats = _beam.DoclingParseStats
DoclingParseResult = _beam.DoclingParseResult

def _get_docling_pdf_backend() -> str:
    """
    Select the Docling processing backend (beam/local). Defaults to beam.
    """
    backend = (os.getenv("DOCLING_BACKEND_SELECTION", "beam") or "").strip().lower()
    return backend if backend in {"beam", "local"} else "beam"


def parse_pdf_with_docling_preview(
    pdf_bytes: bytes,
    file_name: str,
    artifact_root: Path | None = None,
    page_chunk_size: int = DEFAULT_DOCLING_PAGE_CHUNK_SIZE,
) -> DoclingParseResult:
    """
    Public Docling PDF preview entrypoint that dispatches to Beam or local backend.
    """
    backend = _get_docling_pdf_backend() # Select which mode to run (Either local or beam)
    if backend == "local":
        print("Extracting PDF locally")
        from app.service.rag.ingestion.docling_pdf_extractor_local import (
            parse_pdf_with_docling_preview_local,
        )

        return parse_pdf_with_docling_preview_local(
            pdf_bytes=pdf_bytes,
            file_name=file_name,
            artifact_root=artifact_root,
            page_chunk_size=page_chunk_size,
        )

    print("Extracting PDF using Beam endpoint")
    return _beam.parse_pdf_with_docling_preview(
        pdf_bytes=pdf_bytes,
        file_name=file_name,
        artifact_root=artifact_root,
    )


def get_pdf_ingestion_strategy() -> str:
    """
    Determine the PDF ingestion strategy based on environment variable.
    """
    strategy = os.getenv("INGEST_PDF_EXTRACTOR", "legacy").strip().lower() or "legacy"

    print(
        f"PDF ingestion strategy: {strategy} (set with INGEST_PDF_EXTRACTOR environment variable, defaults to 'legacy')"
    )
    return strategy if strategy in {"legacy", "docling"} else "legacy"


__all__ = [
    "DEFAULT_DOCLING_PAGE_CHUNK_SIZE",
    "DOCLING_IMAGE_PLACEHOLDER",
    "DOCLING_IMAGE_CROP_FAILED_MARKER",
    "ExtractedImageArtifact",
    "DoclingChunkFailure",
    "DoclingParseStats",
    "DoclingParseResult",
    "parse_pdf_with_docling_preview",
    "get_pdf_ingestion_strategy",
]
