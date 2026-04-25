"""LLM client adapter for the agentic query action loop.

The runtime expects an OpenAI-compatible `/chat/completions` endpoint.
This wrapper centralizes URL/model/key resolution and response parsing.
"""

from __future__ import annotations

import os
from typing import Any


def _normalize_url(raw: str) -> str:
    """Normalize a base URL into a chat-completions endpoint URL."""

    url = str(raw or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    return f"{url}/chat/completions"


def _resolve_runtime_config() -> tuple[str, str | None, str]:
    """Resolve endpoint/key/model from env vars with backward-compatible fallbacks."""

    url = _normalize_url(
        os.getenv("AGENTIC_QUERY_LLM_URL")
        or os.getenv("MOD_AGENT_LLM_URL")
        or "https://api.deepseek.com/v1/chat/completions"
    )
    api_key = os.getenv("AGENTIC_QUERY_LLM_KEY") or os.getenv("MOD_AGENT_LLM_KEY")
    model = (
        os.getenv("AGENTIC_QUERY_LLM_MODEL")
        or os.getenv("MOD_AGENT_LLM_MODEL")
        or "deepseek-chat"
    )
    return url, api_key, model


def _extract_usage(data: dict[str, Any]) -> dict[str, int]:
    """Extract token usage metrics from providers with slightly different schemas."""

    usage = data.get("usage") if isinstance(data, dict) else {}
    if not isinstance(usage, dict):
        usage = {}

    # Handle both classic and prompt-cache usage fields.
    prompt_tokens = int(
        usage.get("prompt_tokens", 0)
        or (
            int(usage.get("prompt_cache_hit_tokens", 0) or 0)
            + int(usage.get("prompt_cache_miss_tokens", 0) or 0)
        )
    )
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(
        usage.get("total_tokens", prompt_tokens + completion_tokens)
        or prompt_tokens
        + completion_tokens
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


async def call_action_model(
    *,
    messages: list[dict[str, Any]],
    session: Any = None,
    max_tokens: int = 512,
    timeout_s: float = 120.0,
) -> tuple[str, dict[str, int]]:
    """Call the configured action model and return `(text_content, token_usage)`."""
    import aiohttp

    url, api_key, model = _resolve_runtime_config()
    if not api_key:
        raise RuntimeError("AGENTIC_QUERY_LLM_KEY (or MOD_AGENT_LLM_KEY fallback) is not set.")

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": int(max_tokens),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=float(timeout_s))

    async def _do_request(http_session: Any) -> dict[str, Any]:
        """Run one HTTP request using a caller-provided aiohttp session."""

        async with http_session.post(
            url,
            json=payload,
            headers=headers,
            timeout=timeout,
        ) as response:
            if response.status != 200:
                body = await response.text()
                raise RuntimeError(f"Agentic query LLM API error ({response.status}): {body}")
            data = await response.json()
            if not isinstance(data, dict):
                raise RuntimeError("Agentic query LLM returned a non-JSON object response.")
            return data

    if session is not None:
        data = await _do_request(session)
    else:
        async with aiohttp.ClientSession() as own_session:
            data = await _do_request(own_session)

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Agentic query LLM returned empty choices.")

    first_choice = choices[0] if isinstance(choices[0], dict) else {}
    message = first_choice.get("message") if isinstance(first_choice, dict) else {}
    content = str(message.get("content") or "").strip() if isinstance(message, dict) else ""
    if not content:
        raise RuntimeError("Agentic query LLM returned empty message content.")
    return content, _extract_usage(data)
