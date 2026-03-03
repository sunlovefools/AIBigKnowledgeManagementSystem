"""Answer generation package for provider-agnostic RAG answer synthesis.

This package organizes answer generation into focused modules (config, providers,
normalization, transport, and orchestration) for maintainability and testability.
It should not expose provider internals at import sites; callers should use
`generate_answer` / `generate_answer_api`.
"""

from __future__ import annotations

from typing import Any

__all__ = ["generate_answer", "generate_answer_api"]


async def generate_answer(rag_docs: list[dict[str, Any]], user_query: str) -> str:
    """Lazily import orchestration entrypoint and delegate answer generation.

    Args:
        rag_docs: Retrieved RAG documents as dictionaries.
        user_query: Raw user query text.

    Returns:
        Generated answer text.
    """
    from .orchestration import generate_answer as _generate_answer

    return await _generate_answer(rag_docs, user_query)


async def generate_answer_api(rag_docs: list[dict[str, Any]], user_query: str) -> str:
    """Compatibility wrapper mirroring historical API naming.

    Args:
        rag_docs: Retrieved RAG documents as dictionaries.
        user_query: Raw user query text.

    Returns:
        Generated answer text.
    """
    return await generate_answer(rag_docs, user_query)
