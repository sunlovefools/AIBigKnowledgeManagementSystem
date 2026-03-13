"""Compatibility facade for answer generation entrypoints.

This module keeps legacy imports stable while delegating all implementation logic to
`answer_generation.orchestration`. It exists to preserve caller compatibility and should
not contain provider, transport, or normalization business logic.
"""

from __future__ import annotations

from typing import Any

__all__ = ["generate_answer", "generate_answer_api"]


async def generate_answer(rag_docs: list[dict[str, Any]], user_query: str) -> str:
    """Forward answer generation calls to the refactored orchestration module.

    Args:
        rag_docs: Retrieved RAG documents as dictionaries.
        user_query: Raw user query text.

    Returns:
        Generated answer text from the selected provider.
    """
    # Keep lazy import to avoid loading orchestration dependencies at import time.
    from .answer_generation.orchestration import generate_answer as _generate_answer

    return await _generate_answer(rag_docs, user_query)


async def generate_answer_api(rag_docs: list[dict[str, Any]], user_query: str) -> str:
    """Compatibility wrapper that preserves historical `generate_answer_api` usage.

    Args:
        rag_docs: Retrieved RAG documents as dictionaries.
        user_query: Raw user query text.

    Returns:
        Generated answer text.
    """
    return await generate_answer(rag_docs, user_query)
