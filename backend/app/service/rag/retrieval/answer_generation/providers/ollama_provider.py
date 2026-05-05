"""Ollama provider implementation for answer generation.

This module handles local Ollama SDK usage and HTTP fallback for Ollama-compatible
`/api/generate` endpoints. It exists to isolate Ollama-specific behavior. It should
not load environment configuration directly outside explicit fallback host handling.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import aiohttp

from ...prompts.answer_generator_prompt import (
    SYSTEM_PROMPT,
    build_user_message,
)
from ..config import is_local_ollama_url
from ..context_normalizer import build_llm_context_payload
from ..http_client import post_json
from ..logging_adapter import log_llm_request, log_llm_response
from ..models import (
    DEFAULT_OLLAMA_HOST,
    NO_ANSWER_FALLBACK,
    AnswerGeneratorConfig,
    NormalizedRagDoc,
)


def coerce_ollama_response_dict(data: Any, error_prefix: str) -> dict[str, Any]:
    """Normalize Ollama SDK responses into a dict-like shape.

    Args:
        data: Ollama SDK response object or dictionary.
        error_prefix: Error message prefix used for normalization.

    Returns:
        Dictionary containing at least response fields when available.

    Raises:
        RuntimeError: If response cannot be coerced into a dictionary.
    """
    if isinstance(data, dict):
        return data

    model_dump = getattr(data, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            return dumped

    to_dict = getattr(data, "dict", None)
    if callable(to_dict):
        dumped = to_dict()
        if isinstance(dumped, dict):
            return dumped

    response_text = getattr(data, "response", None)
    if isinstance(response_text, str):
        return {"response": response_text}

    raise RuntimeError(f"{error_prefix}: expected dict-like response, got {type(data).__name__}.")


async def generate_via_ollama(
    session: aiohttp.ClientSession,
    cfg: AnswerGeneratorConfig,
    rag_docs: list[NormalizedRagDoc],
    user_query: str,
) -> str:
    """Generate an answer through local Ollama SDK or HTTP `/api/generate` fallback.

    Args:
        session: Active aiohttp session.
        cfg: Loaded answer-generator runtime config.
        rag_docs: Normalized RAG docs.
        user_query: Raw user query.

    Returns:
        Provider answer string or fallback answer text.

    Raises:
        RuntimeError: On missing model/dependencies or provider call failures.
    """
    if not cfg.model:
        raise RuntimeError(
            "Ollama answer generator model is missing. "
            "Set OLLAMA_ANSWER_GENERATOR_LLM_MODEL "
            "(or LOCAL_ANSWER_GENERATOR_LLM_MODEL / OLLAMA_MODEL)."
        )

    # Build the reduced numbered context payload.
    rag_context_payload = build_llm_context_payload(rag_docs)
    prompt_text = build_user_message(rag_context_payload, user_query)

    log_llm_request(
        provider="OLLAMA",
        model=cfg.model,
        user_query=user_query,
        rag_context_payload=rag_context_payload,
    )

    payload = {
        "model": cfg.model,
        "system": SYSTEM_PROMPT,
        "prompt": prompt_text,
        "stream": False,
        "enable_thinking": False,
        "options": {
            "num_gpu": 99,
            "temperature": 0
        },
    }

    # BEAM is an Ollama-compatible alias but should always use HTTP so auth headers are applied.
    is_local = cfg.provider.strip().upper() == "OLLAMA" and is_local_ollama_url(cfg.url)
    swap_to_ram = bool(cfg.ollama_swap_to_ram and is_local)
    if swap_to_ram:
        # Ollama does not expose direct GPU->RAM migration. keep_alive=0 unloads model
        # after response, and next request auto-loads it back for inference.
        payload["keep_alive"] = 0

    default_local_url = os.getenv("OLLAMA_HOST", "").strip() or f"{DEFAULT_OLLAMA_HOST.rstrip('/')}/api/generate"

    if is_local:
        try:
            from ollama import AsyncClient
        except ModuleNotFoundError as exc:
            # Keep local behavior functional even when the optional SDK is unavailable.
            target_url = cfg.url or default_local_url
            headers = {"Content-Type": "application/json"}
            if cfg.provider.strip().upper() == "BEAM":
                headers["Authorization"] = f"Bearer {cfg.api_key}"
            try:
                data = await post_json(
                    session=session,
                    url=target_url,
                    payload=payload,
                    headers=headers,
                    timeout_s=cfg.timeout_s,
                    error_prefix="Answer Generator Ollama API error",
                )
            except RuntimeError as http_exc:
                raise RuntimeError(
                    "Missing required dependency 'ollama' and HTTP fallback failed. "
                    "Install `ollama` package or ensure a reachable Ollama API URL via "
                    "OLLAMA_ANSWER_GENERATOR_LLM_URL (or OLLAMA_HOST). "
                    f"Details: {http_exc}"
                ) from exc

            answer = data.get("response")
            final_answer = answer if isinstance(answer, str) and answer.strip() else NO_ANSWER_FALLBACK
            log_llm_response(final_answer)
            return final_answer

        try:
            client = AsyncClient()
            generate_kwargs: dict[str, Any] = {
                "model": cfg.model,
                "system": SYSTEM_PROMPT,
                "prompt": prompt_text,
                "stream": False,
                "options": payload["options"],
            }
            if swap_to_ram:
                generate_kwargs["keep_alive"] = 0

            data = await asyncio.wait_for(
                client.generate(**generate_kwargs),
                timeout=cfg.timeout_s,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"Answer Generator Ollama library error: request timed out after {cfg.timeout_s} seconds."
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Answer Generator Ollama library error: {exc}") from exc

        data_dict = coerce_ollama_response_dict(
            data,
            error_prefix="Answer Generator Ollama library error",
        )
    else:
        if not cfg.url:
            if cfg.provider.strip().upper() == "BEAM":
                raise RuntimeError(
                    "BEAM_ANSWER_GENERATOR_LLM_URL is required when "
                    "ANSWER_GENERATOR_LLM_PROVIDER=BEAM."
                )
            raise RuntimeError("Ollama answer generator URL is missing. Set OLLAMA_ANSWER_GENERATOR_LLM_URL.")

        headers = {"Content-Type": "application/json"}
        if cfg.provider.strip().upper() == "BEAM":
            headers["Authorization"] = f"Bearer {cfg.api_key}"
        data_dict = await post_json(
            session=session,
            url=cfg.url,
            payload=payload,
            headers=headers,
            timeout_s=cfg.timeout_s,
            error_prefix="Answer Generator Ollama API error",
        )

    print(data_dict)
    answer = data_dict.get("response")
    print(answer)
    final_answer = answer if isinstance(answer, str) and answer.strip() else NO_ANSWER_FALLBACK
    log_llm_response(final_answer)
    return final_answer


class OllamaAnswerProvider:
    """Adapter class implementing the provider protocol for Ollama execution."""

    async def generate(
        self,
        session: aiohttp.ClientSession,
        cfg: AnswerGeneratorConfig,
        rag_docs: list[NormalizedRagDoc],
        user_query: str,
    ) -> str:
        """Generate an answer through the Ollama provider path."""
        return await generate_via_ollama(session, cfg, rag_docs, user_query)
