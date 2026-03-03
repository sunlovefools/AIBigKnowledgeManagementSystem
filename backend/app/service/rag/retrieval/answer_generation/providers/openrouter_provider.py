"""OpenRouter provider implementation for answer generation.

This module builds OpenRouter Chat Completions payloads and parses structured
responses into final answer text with source suffix handling. It should not perform
configuration loading or retrieval-context normalization.
"""

from __future__ import annotations

import json

import aiohttp

from ...prompts.answer_generator_prompt import (
    SYSTEM_PROMPT,
    build_user_message_json_context,
)
from ..citations import (
    append_or_replace_sources_suffix,
    collect_source_file_names,
)
from ..http_client import post_json
from ..logging_adapter import log_llm_request, log_llm_response
from ..models import NO_ANSWER_FALLBACK, AnswerGeneratorConfig, NormalizedRagDoc


async def generate_via_openrouter(
    session: aiohttp.ClientSession,
    cfg: AnswerGeneratorConfig,
    rag_docs: list[NormalizedRagDoc],
    user_query: str,
) -> str:
    """Generate an answer via OpenRouter chat completions.

    Args:
        session: Active aiohttp session.
        cfg: Loaded answer-generator runtime config.
        rag_docs: Normalized RAG docs.
        user_query: Raw user query.

    Returns:
        Final answer text with canonical sources suffix.

    Raises:
        RuntimeError: If API key is missing or API call fails.
    """
    if not cfg.api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required when ANSWER_GENERATOR_LLM_PROVIDER=OPENROUTER.")
    if not cfg.url:
        raise RuntimeError("OPENROUTER_URL is missing when ANSWER_GENERATOR_LLM_PROVIDER=OPENROUTER.")

    rag_context_json = json.dumps(rag_docs, ensure_ascii=False, indent=2)
    log_llm_request(
        provider="OPENROUTER",
        model=cfg.model,
        user_query=user_query,
        rag_context_payload=rag_context_json,
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_message_json_context(rag_docs, user_query)},
    ]

    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": 0,
    }

    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }

    data = await post_json(
        session=session,
        url=cfg.url,
        payload=payload,
        headers=headers,
        timeout_s=cfg.timeout_s,
        error_prefix="Answer Generator OpenRouter API error",
    )

    source_names = collect_source_file_names(rag_docs)
    choices = data.get("choices")

    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        message = first_choice.get("message") if isinstance(first_choice, dict) else {}
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                final_answer = append_or_replace_sources_suffix(content, source_names)
                log_llm_response(final_answer)
                return final_answer

    final_fallback = append_or_replace_sources_suffix(NO_ANSWER_FALLBACK, source_names)
    log_llm_response(final_fallback)
    return final_fallback


class OpenRouterAnswerProvider:
    """Adapter class implementing the provider protocol for OpenRouter execution."""

    async def generate(
        self,
        session: aiohttp.ClientSession,
        cfg: AnswerGeneratorConfig,
        rag_docs: list[NormalizedRagDoc],
        user_query: str,
    ) -> str:
        """Generate an answer through the OpenRouter provider path."""
        return await generate_via_openrouter(session, cfg, rag_docs, user_query)
