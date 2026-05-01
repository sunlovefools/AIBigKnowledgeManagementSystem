"""Shared helpers for canonical text-LLM environment resolution."""

from __future__ import annotations

import os


DEFAULT_LLM_API_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-v4-flash"
DEFAULT_LLM_THINKING = "disabled"


def normalize_chat_completions_url(raw: str) -> str:
    """Normalize a base URL into a chat-completions endpoint URL."""

    url = str(raw or "").strip().rstrip("/")
    if not url:
        return f"{DEFAULT_LLM_API_BASE_URL}/chat/completions"
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    if url.endswith("/api"):
        return f"{url}/v1/chat/completions"
    return f"{url}/chat/completions"


def resolve_llm_api_url(*fallbacks: str | None) -> str:
    """Resolve canonical LLM API URL, falling back to legacy names when needed."""

    raw = os.getenv("LLM_API_URL")
    if isinstance(raw, str) and raw.strip():
        return normalize_chat_completions_url(raw)

    for raw in fallbacks:
        if isinstance(raw, str) and raw.strip():
            return normalize_chat_completions_url(raw)

    return normalize_chat_completions_url(DEFAULT_LLM_API_BASE_URL)


def resolve_llm_api_key(*fallbacks: str | None) -> str | None:
    """Resolve canonical LLM API key, falling back to legacy names when needed."""

    raw = os.getenv("LLM_API_KEY")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    for raw in fallbacks:
        if isinstance(raw, str) and raw.strip():
            return raw.strip()

    return None


def resolve_llm_model(*fallbacks: str | None, default: str = DEFAULT_LLM_MODEL) -> str:
    """Resolve canonical LLM model, falling back to legacy names when needed."""

    raw = os.getenv("LLM_MODEL")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    for raw in fallbacks:
        if isinstance(raw, str) and raw.strip():
            return raw.strip()

    return default


def resolve_llm_thinking(*fallbacks: str | None, url: str | None = None, model: str | None = None) -> str:
    """Resolve canonical thinking mode, defaulting to disabled for DeepSeek."""

    raw = os.getenv("LLM_THINKING")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()

    for raw in fallbacks:
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()

    normalized_url = str(url or "").casefold()
    normalized_model = str(model or "").casefold()
    if "deepseek" in normalized_url or "deepseek" in normalized_model:
        return DEFAULT_LLM_THINKING
    return ""
