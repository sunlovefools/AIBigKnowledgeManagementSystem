"""
Deprecated compatibility shim for Docling extraction imports.

TODO(remove-after-release): Delete this module after one release cycle once
external imports have migrated to `app.service.rag.ingestion.docling`.
"""

from __future__ import annotations

import warnings

from app.service.rag.ingestion import docling as _docling

warnings.warn(
    (
        "`app.service.rag.ingestion.docling_pdf_extractor` is deprecated and will "
        "be removed in a future release. "
        "Use `app.service.rag.ingestion.docling` instead."
    ),
    DeprecationWarning,
    stacklevel=2,
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


parse_pdf_with_docling = _docling.parse_pdf_with_docling


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
    "parse_pdf_with_docling",
    "get_pdf_ingestion_strategy",
]

