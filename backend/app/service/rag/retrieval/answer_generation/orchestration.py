"""Top-level orchestration for answer generation.

This module coordinates config loading, context normalization, provider selection,
and provider execution under a shared aiohttp session. It exists as the single entry
point for answer generation. It should not contain provider-specific request parsing.
"""

from __future__ import annotations

from typing import Any

import aiohttp

from .config import load_answer_generator_config
from .context_normalizer import normalize_rag_docs
from .models import AnswerGeneratorConfig
from .providers import AnswerProvider, OllamaAnswerProvider, OpenRouterAnswerProvider

__all__ = ["generate_answer", "generate_answer_api"]


def _resolve_provider(cfg: AnswerGeneratorConfig) -> AnswerProvider:
    """Resolve provider implementation from runtime config.

    Args:
        cfg: Loaded runtime answer-generator config.

    Returns:
        Provider implementation satisfying `AnswerProvider` protocol.

    Raises:
        RuntimeError: If provider name is unsupported.
    """
    # Normalize provider name for case-insensitive matching and alias support.
    provider_name = cfg.provider.strip().upper()

    # Based on the provider name, return the appropriate provider instance.
    if provider_name in {"OLLAMA", "BEAM"}:
        return OllamaAnswerProvider()
    if provider_name == "OPENROUTER":
        return OpenRouterAnswerProvider()

    raise RuntimeError(
        f"Invalid ANSWER_GENERATOR_LLM_PROVIDER: {cfg.provider}. "
        "Expected 'OLLAMA', 'BEAM' (alias), or 'OPENROUTER'."
    )


async def generate_answer(rag_docs: list[dict[str, Any]], user_query: str) -> str:
    """Generate an answer by routing through the configured provider.

    Args:
        rag_docs: Retrieved parent documents as dictionaries.
        user_query: End-user query text.

    Returns:
        Final generated answer string.
    """
    cfg = load_answer_generator_config()
    normalized_docs = normalize_rag_docs(rag_docs)
    
    # Get the correct provider based on the loaded configuration.
    provider = _resolve_provider(cfg)

    async with aiohttp.ClientSession() as session:
        return await provider.generate(session, cfg, normalized_docs, user_query)


async def generate_answer_api(rag_docs: list[dict[str, Any]], user_query: str) -> str:
    """Compatibility wrapper for callers using `generate_answer_api`.

    Args:
        rag_docs: Retrieved parent documents as dictionaries.
        user_query: End-user query text.

    Returns:
        Final generated answer string.
    """
    return await generate_answer(rag_docs, user_query)
