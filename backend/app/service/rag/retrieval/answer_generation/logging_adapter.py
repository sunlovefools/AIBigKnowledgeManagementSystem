"""Debug logging adapter for answer generation request/response payloads.

This module wraps debug logger imports and keeps logging integration isolated from
provider business logic. It should not transform answer content beyond direct pass-through.
"""

from __future__ import annotations

try:
    from backend.debug.debug_logger import (
        log_answer_generation_request,
        log_answer_generation_response,
    )
except ImportError:
    from debug.debug_logger import (
        log_answer_generation_request,
        log_answer_generation_response,
    )


def log_llm_request(provider: str, model: str | None, user_query: str, rag_context_payload: str) -> None:
    """Write safe LLM request debug info.

    Args:
        provider: Provider name string.
        model: Provider model identifier.
        user_query: End-user query string.
        rag_context_payload: Serialized context payload sent to provider.
    """
    log_answer_generation_request(
        provider=provider,
        model=model,
        user_query=user_query,
        rag_context=rag_context_payload,
    )


def log_llm_response(answer: str) -> None:
    """Write safe LLM response debug info.

    Args:
        answer: Final answer text returned from provider logic.
    """
    log_answer_generation_response(answer=answer)
