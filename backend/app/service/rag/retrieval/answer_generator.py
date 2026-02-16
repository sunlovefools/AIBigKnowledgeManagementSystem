"""Answer generator client with provider-agnostic public API and structured debug logs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any

import aiohttp

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
from .prompts.answer_generator_prompt import SYSTEM_PROMPT, build_user_message_json_context

__all__ = ["generate_answer", "generate_answer_api"]

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_DEFAULT_TIMEOUT_S = 120.0
_NO_ANSWER_FALLBACK = "No answer returned by Answer Generator"
_SOURCES_SUFFIX_UNKNOWN = "(Sources: filename unknown)"


@dataclass(frozen=True)
class _AnswerGeneratorConfig:
    """Runtime configuration for answer generation providers."""

    provider: str
    timeout_s: float
    beam_url: str | None
    beam_key: str | None
    openrouter_url: str
    openrouter_api_key: str | None
    openrouter_model: str


def _load_config() -> _AnswerGeneratorConfig:
    """Load environment-based config at call time."""
    provider = os.getenv("ANSWER_GENERATOR_LLM_PROVIDER", "BEAM").strip().upper()
    timeout_raw = (
        os.getenv("ANSWER_GENERATOR_TIMEOUT_S")
        or os.getenv("ANSWER_GENERATOR_TIMEOUT")
        or str(_DEFAULT_TIMEOUT_S)
    ).strip()

    try:
        timeout_s = float(timeout_raw)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid answer generator timeout value {timeout_raw!r}. Expected a number."
        ) from exc
    if timeout_s <= 0:
        raise RuntimeError("Answer generator timeout must be > 0.")

    beam_url = os.getenv("BEAM_ANSWER_GENERATOR_LLM_URL") or os.getenv("LOCAL_ANSWER_GENERATOR_LLM_URL")
    beam_key = os.getenv("BEAM_ANSWER_GENERATOR_LLM_KEY") or os.getenv("LOCAL_ANSWER_GENERATOR_LLM_KEY")

    openrouter_url = os.getenv("OPENROUTER_URL", _OPENROUTER_URL).strip() or _OPENROUTER_URL
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
    openrouter_model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-r1:free").strip()

    return _AnswerGeneratorConfig(
        provider=provider,
        timeout_s=timeout_s,
        beam_url=beam_url,
        beam_key=beam_key,
        openrouter_url=openrouter_url,
        openrouter_api_key=openrouter_api_key,
        openrouter_model=openrouter_model,
    )


def _normalize_rag_docs(rag_docs: list[dict[str, Any]] | list[str]) -> list[dict[str, Any]]:
    """Normalize mixed RAG input into JSON-serializable document dictionaries."""
    normalized: list[dict[str, Any]] = []

    for item in rag_docs:
        if isinstance(item, dict):
            page_content = item.get("page_content", "")
            metadata = _extract_minimal_metadata(item.get("metadata", {}))
            normalized.append(
                {
                    "id": item.get("id"),
                    "metadata": metadata,
                    "page_content": str(page_content) if page_content is not None else "",
                    "type": str(item.get("type", "Document")),
                }
            )
        else:
            normalized.append(
                {
                    "id": None,
                    "metadata": {},
                    "page_content": str(item),
                    "type": "Document",
                }
            )

    return normalized


def _extract_minimal_metadata(metadata: Any) -> dict[str, Any]:
    """Keep only metadata fields needed for answer citation and traceability."""
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

    minimal_metadata: dict[str, Any] = {}
    if isinstance(file_name, str) and file_name.strip():
        minimal_metadata["file_name"] = file_name.strip()
    if isinstance(parent_chunk_number, int):
        minimal_metadata["parent_chunk_number"] = parent_chunk_number

    return minimal_metadata


def _collect_source_file_names(rag_docs: list[dict[str, Any]]) -> list[str]:
    """Collect unique source file names from normalized docs while preserving order."""
    seen: set[str] = set()
    source_names: list[str] = []

    for doc in rag_docs:
        if not isinstance(doc, dict):
            continue
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


def _format_sources_suffix(source_names: list[str]) -> str:
    """Build a deterministic sources suffix for final answers."""
    if not source_names:
        return _SOURCES_SUFFIX_UNKNOWN
    return f"(Sources: {', '.join(source_names)})"


def _append_or_replace_sources_suffix(answer_text: str, source_names: list[str]) -> str:
    """Ensure the final answer ends with one canonical sources suffix."""
    base = answer_text.strip() if isinstance(answer_text, str) and answer_text.strip() else _NO_ANSWER_FALLBACK
    base = re.sub(r"\s*\(Sources:\s*[^)]*\)\s*$", "", base).strip()
    sources_suffix = _format_sources_suffix(source_names)
    return f"{base} {sources_suffix}".strip()


async def _post_json(
    session: aiohttp.ClientSession,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_s: float,
    error_prefix: str,
) -> dict[str, Any]:
    """POST JSON and return parsed JSON with normalized RuntimeError handling."""
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with session.post(url, json=payload, headers=headers, timeout=timeout) as response:
            if response.status != 200:
                response_text = await response.text()
                raise RuntimeError(f"{error_prefix} ({response.status}): {response_text}")

            data = await response.json()
            if not isinstance(data, dict):
                raise RuntimeError(f"{error_prefix}: expected JSON object response.")
            return data
    except TimeoutError as exc:
        raise RuntimeError(f"{error_prefix}: request timed out after {timeout_s} seconds.") from exc
    except aiohttp.ClientError as exc:
        raise RuntimeError(f"{error_prefix}: HTTP client error: {exc}") from exc


def _log_llm_request(
    provider: str,
    model: str | None,
    user_query: str,
    rag_context_json: str,
) -> None:
    """Write safe LLM request debug info."""
    log_answer_generation_request(
        provider=provider,
        model=model,
        user_query=user_query,
        rag_context=rag_context_json,
    )


def _log_llm_response(answer: str) -> None:
    """Write safe LLM response debug info."""
    log_answer_generation_response(answer=answer)


async def _generate_via_beam(
    session: aiohttp.ClientSession,
    cfg: _AnswerGeneratorConfig,
    rag_docs: list[dict[str, Any]],
    user_query: str,
) -> str:
    """Generate an answer using the BEAM/local endpoint."""
    if not cfg.beam_url or not cfg.beam_key:
        raise RuntimeError(
            "BEAM provider configuration missing. Set BEAM_ANSWER_GENERATOR_LLM_URL "
            "(or LOCAL_ANSWER_GENERATOR_LLM_URL) and BEAM_ANSWER_GENERATOR_LLM_KEY "
            "(or LOCAL_ANSWER_GENERATOR_LLM_KEY)."
        )

    rag_context_json = json.dumps(rag_docs, ensure_ascii=False, indent=2)
    _log_llm_request(provider="BEAM", model=None, user_query=user_query, rag_context_json=rag_context_json)

    # Send rag_context in JSON doc-list form. Beam handler can keep backward compatibility.
    payload = {"rag_context": rag_docs, "user_query": user_query}
    headers = {"Authorization": f"Bearer {cfg.beam_key}", "Content-Type": "application/json"}

    data = await _post_json(
        session=session,
        url=cfg.beam_url,
        payload=payload,
        headers=headers,
        timeout_s=cfg.timeout_s,
        error_prefix="Answer Generator BEAM API error",
    )

    answer = data.get("answer")
    final_answer = answer if isinstance(answer, str) and answer.strip() else _NO_ANSWER_FALLBACK
    _log_llm_response(final_answer)
    return final_answer


async def _generate_via_openrouter(
    session: aiohttp.ClientSession,
    cfg: _AnswerGeneratorConfig,
    rag_docs: list[dict[str, Any]],
    user_query: str,
) -> str:
    """Generate an answer via OpenRouter Chat Completions."""
    if not cfg.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required when ANSWER_GENERATOR_LLM_PROVIDER=OPENROUTER.")

    rag_context_json = json.dumps(rag_docs, ensure_ascii=False, indent=2)
    _log_llm_request(
        provider="OPENROUTER",
        model=cfg.openrouter_model,
        user_query=user_query,
        rag_context_json=rag_context_json,
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_message_json_context(rag_docs, user_query)},
    ]

    payload = {
        "model": cfg.openrouter_model,
        "messages": messages, 
        "temperature": 0
    }
    
    headers = {
        "Authorization": f"Bearer {cfg.openrouter_api_key}",
        "Content-Type": "application/json",
    }

    data = await _post_json(
        session=session,
        url=cfg.openrouter_url,
        payload=payload,
        headers=headers,
        timeout_s=cfg.timeout_s,
        error_prefix="Answer Generator OpenRouter API error",
    )

    source_names = _collect_source_file_names(rag_docs)

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        message = first_choice.get("message") if isinstance(first_choice, dict) else {}
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                final_answer = _append_or_replace_sources_suffix(content, source_names)
                _log_llm_response(final_answer)
                return final_answer

    final_fallback = _append_or_replace_sources_suffix(_NO_ANSWER_FALLBACK, source_names)
    _log_llm_response(final_fallback)
    return final_fallback


async def generate_answer(rag_docs: list[dict[str, Any]] | list[str], user_query: str) -> str:
    """
    Public entry point for generating an answer with configured provider.

    Accepts normalized RAG docs (preferred) and remains backward compatible with list[str].
    """
    cfg = _load_config()
    normalized_docs = _normalize_rag_docs(rag_docs)

    async with aiohttp.ClientSession() as session:
        if cfg.provider == "BEAM":
            return await _generate_via_beam(session, cfg, normalized_docs, user_query)
        if cfg.provider == "OPENROUTER":
            return await _generate_via_openrouter(session, cfg, normalized_docs, user_query)
        raise RuntimeError(
            f"Invalid ANSWER_GENERATOR_LLM_PROVIDER: {cfg.provider}. "
            "Expected 'BEAM' or 'OPENROUTER'."
        )


async def generate_answer_api(rag_docs: list[dict[str, Any]] | list[str], user_query: str) -> str:
    """Compatibility wrapper for callers using generate_answer_api."""
    return await generate_answer(rag_docs, user_query)
