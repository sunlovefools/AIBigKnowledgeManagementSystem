"""
Docling configuration constants and environment-backed toggles.
"""

from __future__ import annotations

import os


DEFAULT_DOCLING_PAGE_CHUNK_SIZE = 6
DOCLING_IMAGE_PLACEHOLDER = "<!-- image -->"
DOCLING_IMAGE_CROP_FAILED_MARKER = "<!-- image-crop-failed -->"

BEAM_DOCLING_TIMEOUT_SECONDS = 600
BEAM_DOCLING_CLIENT_MAX_FILE_SIZE_MB = 25
BEAM_DOCLING_CLIENT_CROP_SCALE = 2.5

LOCAL_DOCLING_CHUNK_SIZE = 6
LOCAL_DOCLING_LAYOUT_BATCH_SIZE = 16
LOCAL_DOCLING_TABLE_BATCH_SIZE = 16


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return default


def is_docling_artifacts_enabled() -> bool:
    """
    Return whether Docling debug artifacts should be persisted.

    Env: DOCLING_ARTIFACTS_ENABLED
    Missing or invalid values default to True.
    """

    return _parse_bool_env("DOCLING_ARTIFACTS_ENABLED", default=True)


def get_docling_backend_selection() -> str:
    """
    Select the Docling processing backend (beam/local). Defaults to beam.
    """

    backend = (os.getenv("DOCLING_BACKEND_SELECTION", "beam") or "").strip().lower()
    return backend if backend in {"beam", "local"} else "beam"


def get_pdf_ingestion_strategy() -> str:
    """
    Determine the PDF ingestion strategy based on environment variable.
    """

    strategy = os.getenv("INGEST_PDF_EXTRACTOR", "legacy").strip().lower() or "legacy"
    print(
        "PDF ingestion strategy: "
        f"{strategy} (set with INGEST_PDF_EXTRACTOR environment variable, defaults to 'legacy')"
    )
    return strategy if strategy in {"legacy", "docling"} else "legacy"

