"""RAG context normalization helpers for answer generation.

This module converts retrieval payloads into a strict, serializable document
shape consumed by provider logic. It exists to isolate data-shape cleanup and
input validation. It should not read environment variables or call external APIs.
"""

from __future__ import annotations

from typing import Any

from .models import NormalizedMetadata, NormalizedRagDoc


def extract_minimal_metadata(metadata: Any) -> NormalizedMetadata:
    """Keep only metadata fields needed for citation and traceability.

    Args:
        metadata: Raw metadata payload from retrieval layer.

    Returns:
        Metadata containing only `file_name` and `parent_chunk_number` when present.
    """
    if not isinstance(metadata, dict):
        return {}

    file_name = None
    parent_chunk_number = None

    file_metadata = metadata.get("file_metadata")
    if isinstance(file_metadata, dict):
        file_name = file_metadata.get("file_name")
    elif isinstance(metadata.get("file_name"), str):
        file_name = metadata.get("file_name")

    parent_chunk_metadata = metadata.get("parent_chunk_metadata")
    if isinstance(parent_chunk_metadata, dict):
        parent_chunk_number = parent_chunk_metadata.get("parent_chunk_number")
    elif "parent_chunk_number" in metadata:
        parent_chunk_number = metadata.get("parent_chunk_number")

    minimal_metadata: NormalizedMetadata = {}
    if isinstance(file_name, str) and file_name.strip():
        minimal_metadata["file_name"] = file_name.strip()
    if isinstance(parent_chunk_number, int):
        minimal_metadata["parent_chunk_number"] = parent_chunk_number

    return minimal_metadata


def normalize_rag_docs(rag_docs: list[dict[str, Any]]) -> list[NormalizedRagDoc]:
    """Normalize strict dict RAG input into typed JSON-serializable documents.

    Args:
        rag_docs: Retrieved context as list of parent document dictionaries.

    Returns:
        List of normalized RAG docs with consistent keys.

    Raises:
        RuntimeError: If any item is not a document dictionary.
    """
    normalized: list[NormalizedRagDoc] = []

    # Enforce a strict contract so upstream callers keep a consistent payload shape.
    for idx, item in enumerate(rag_docs):
        if not isinstance(item, dict):
            raise RuntimeError(
                "Invalid RAG document input at index "
                f"{idx}: expected dict, got {type(item).__name__}."
            )

        page_content = item.get("page_content", "")
        normalized.append(
            {
                "id": item.get("id"),
                "metadata": extract_minimal_metadata(item.get("metadata", {})),
                "page_content": str(page_content) if page_content is not None else "",
                "type": str(item.get("type", "Document")),
            }
        )

    return normalized
