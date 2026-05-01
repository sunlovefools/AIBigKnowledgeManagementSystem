"""Environment configuration loader for answer generation.

This module resolves provider settings and validates runtime values from environment
variables. It exists to centralize env precedence and URL pass-through handling. It should
not perform network requests or trigger model/provider calls.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from app.service.llm_env import (
    DEFAULT_LLM_MODEL,
    resolve_llm_api_key,
    resolve_llm_api_url,
    resolve_llm_model,
)

from .models import (
    AnswerGeneratorConfig,
    DEFAULT_TIMEOUT_S,
    OPENROUTER_URL_DEFAULT,
)


def _parse_bool_env(name: str, *, default: bool) -> bool:
    """Parse bool-like environment variables with safe defaults."""
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return default


def is_local_ollama_url(url: str | None) -> bool:
    """Return True when the configured Ollama URL targets a local daemon host.

    Args:
        url: Optional configured Ollama URL.

    Returns:
        True when host resolves to localhost/loopback.
    """
    if not url:
        return True

    normalized = url.strip()
    if not normalized:
        return True
    if "://" not in normalized:
        normalized = f"http://{normalized}"

    parsed = urlparse(normalized)
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def load_answer_generator_config() -> AnswerGeneratorConfig:
    """Load and validate answer-generator configuration from environment.

    Returns:
        Fully populated `AnswerGeneratorConfig`.

    Raises:
        RuntimeError: If timeout is invalid or non-positive.
    """

    # Get the provider name (Default is OLLAMA)
    provider = os.getenv("ANSWER_GENERATOR_LLM_PROVIDER", "OLLAMA").strip().upper()

    # Get the timeout value in seconds
    timeout_raw = (
        os.getenv("ANSWER_GENERATOR_TIMEOUT_S")
        or str(DEFAULT_TIMEOUT_S)
    ).strip()

    # Validate timeout early so invalid values fail before provider-specific parsing.
    try:
        timeout_s = float(timeout_raw)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid answer generator timeout value {timeout_raw!r}. Expected a number."
        ) from exc
    if timeout_s <= 0:
        raise RuntimeError("Answer generator timeout must be > 0.")

    # Toggle for local Ollama behavior: unload model from accelerator memory after each request.
    ollama_swap_to_ram = _parse_bool_env("OLLAMA_SWAP_TO_RAM", default=False)

    # If the provider is OPENROUTER, then we will get the API key and the URL for OPENROUTER
    if provider == "OPENROUTER":
        url = resolve_llm_api_url(os.getenv("OPENROUTER_URL", OPENROUTER_URL_DEFAULT))
        model = resolve_llm_model(
            os.getenv("OPENROUTER_MODEL"),
            default=DEFAULT_LLM_MODEL,
        )
        api_key = resolve_llm_api_key(os.getenv("OPENROUTER_API_KEY"))
        if not isinstance(api_key, str) or not api_key.strip():
            raise RuntimeError(
                "LLM_API_KEY (or OPENROUTER_API_KEY fallback) is required when "
                "ANSWER_GENERATOR_LLM_PROVIDER=OPENROUTER."
            )
        api_key = api_key.strip()
    
    # For BEAM provider, the URL and API key are required and must be obtained from BEAM-specific env vars.
    elif provider == "BEAM":
        url_raw = os.getenv("BEAM_ANSWER_GENERATOR_LLM_URL")
        if not isinstance(url_raw, str) or not url_raw.strip():
            raise RuntimeError(
                "BEAM_ANSWER_GENERATOR_LLM_URL is required when "
                "ANSWER_GENERATOR_LLM_PROVIDER=BEAM."
            )
        url = url_raw.strip()

        # BEAM answer generation requires auth key by project convention.
        api_key_raw = os.getenv("BEAM_ANSWER_GENERATOR_LLM_KEY")
        if not isinstance(api_key_raw, str) or not api_key_raw.strip():
            raise RuntimeError(
                "BEAM_ANSWER_GENERATOR_LLM_KEY is required when "
                "ANSWER_GENERATOR_LLM_PROVIDER=BEAM."
            )
        api_key = api_key_raw.strip()
        model = ""  # BEAM does not require a model field in the current implementation.
    # For OLLAMA provider, the URL is optional (SDK mode if omitted) and the API key is not required.
    else:
        # OLLAMA provider can run locally via SDK when URL/API key are omitted.
        url_raw = os.getenv("OLLAMA_ANSWER_GENERATOR_LLM_URL")
        url = url_raw.strip() if isinstance(url_raw, str) and url_raw.strip() else None
        api_key = None
        model_raw = os.getenv("OLLAMA_ANSWER_GENERATOR_LLM_MODEL")
        model = model_raw.strip() if isinstance(model_raw, str) and model_raw.strip() else None

    return AnswerGeneratorConfig(
        provider=provider,
        timeout_s=timeout_s,
        url=url,
        model=model,
        api_key=api_key,
        ollama_swap_to_ram=ollama_swap_to_ram,
    )
