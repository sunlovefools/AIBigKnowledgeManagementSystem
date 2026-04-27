"""Citation utilities for answer generation outputs.

This module extracts source names and applies a canonical answer suffix format.
It exists to keep citation behavior deterministic and testable. It should not
handle provider requests or prompt construction.
"""

from __future__ import annotations

import re

from .models import NO_ANSWER_FALLBACK, SOURCES_SUFFIX_UNKNOWN, NormalizedRagDoc


def collect_source_file_names(rag_docs: list[NormalizedRagDoc]) -> list[str]:
    """Collect unique source file names while preserving first-seen order.

    Args:
        rag_docs: Normalized RAG documents.

    Returns:
        Ordered unique file-name list used for suffix formatting.
    """
    seen: set[str] = set()
    source_names: list[str] = []

    for doc in rag_docs:
        metadata = doc.get("metadata")
        if not isinstance(metadata, dict):
            continue

        file_name = metadata.get("file_name")
        if not isinstance(file_name, str):
            continue

        normalized_name = file_name.strip()
        if not normalized_name or normalized_name in seen:
            continue

        seen.add(normalized_name)
        source_names.append(normalized_name)

    return source_names


def format_sources_suffix(source_names: list[str]) -> str:
    """Build the canonical sources suffix string.

    Args:
        source_names: Ordered source file names.

    Returns:
        Suffix text in `(Sources: ...)` format.
    """
    if not source_names:
        return SOURCES_SUFFIX_UNKNOWN
    return f"(Sources: {', '.join(source_names)})"


def append_or_replace_sources_suffix(answer_text: str, source_names: list[str]) -> str:
    """Ensure answer ends with one canonical sources suffix.

    Args:
        answer_text: LLM-generated answer text.
        source_names: Ordered source file names.

    Returns:
        Answer with a trailing canonical sources suffix.
    """
    base = answer_text.strip() if isinstance(answer_text, str) and answer_text.strip() else NO_ANSWER_FALLBACK

    # Strip an existing trailing sources marker (plain or markdown italic style).
    base = re.sub(r"\s*\*?\(Sources:\s*[^)]*\)\*?\s*$", "", base, flags=re.IGNORECASE).strip()
    if base in {NO_ANSWER_FALLBACK, "No answer found in the provided context."}:
        return base

    sources_suffix = format_sources_suffix(source_names)

    # Keep suffix on a new line to match prompt-level citation formatting intent.
    return f"{base}\n{sources_suffix}"
