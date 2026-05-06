"""LLM client adapter for the agentic query action loop.

The runtime expects an OpenAI-compatible `/chat/completions` endpoint.
This wrapper centralizes URL/model/key resolution and response parsing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from app.service.llm_env import (
    resolve_llm_api_key,
    resolve_llm_api_url,
    resolve_llm_model,
    resolve_llm_thinking,
)


def _normalize_url(raw: str) -> str:
    """Normalize a base URL into a chat-completions endpoint URL."""

    url = str(raw or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    return f"{url}/chat/completions"


@dataclass(frozen=True)
class ActionModelResult:
    """LLM result that remains compatible with historical two-item unpacking."""

    content: str
    usage: dict[str, int]
    assistant_message: dict[str, Any] = field(default_factory=dict)

    def __iter__(self):
        yield self.content
        yield self.usage


def _resolve_runtime_config() -> tuple[str, str | None, str, str]:
    """Resolve endpoint/key/model from env vars with backward-compatible fallbacks."""

    url = resolve_llm_api_url(
        os.getenv("AGENTIC_QUERY_LLM_URL"),
        os.getenv("MOD_AGENT_LLM_URL"),
    )
    api_key = resolve_llm_api_key(
        os.getenv("AGENTIC_QUERY_LLM_KEY"),
        os.getenv("MOD_AGENT_LLM_KEY"),
    )
    model = resolve_llm_model(
        os.getenv("AGENTIC_QUERY_LLM_MODEL")
        or os.getenv("MOD_AGENT_LLM_MODEL")
    )
    thinking = resolve_llm_thinking(
        os.getenv("AGENTIC_QUERY_LLM_THINKING"),
        os.getenv("MOD_AGENT_LLM_THINKING"),
        url=url,
        model=model,
    )
    return url, api_key, model, str(thinking or "").strip().lower()


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


def _stringify_content_part(part: Any) -> str:
    if part is None:
        return ""
    if isinstance(part, str):
        return part.strip()
    if isinstance(part, dict):
        for key in ("text", "content", "value"):
            value = part.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    return str(part).strip()


def _extract_message_text(message: dict[str, Any], choice: dict[str, Any]) -> str:
    candidates: list[Any] = [
        message.get("content"),
        message.get("text"),
        choice.get("text"),
    ]
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if isinstance(function, dict):
                candidates.append(function.get("arguments"))

    parts: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, list):
            parts.extend(
                part
                for part in (_stringify_content_part(item) for item in candidate)
                if part
            )
            continue
        part = _stringify_content_part(candidate)
        if part:
            parts.append(part)

    return "\n".join(parts).strip()


def _assistant_message_for_transcript(message: dict[str, Any], content: str) -> dict[str, Any]:
    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str):
            if value.strip():
                assistant_message[key] = value
            continue
        if value:
            assistant_message[key] = value
    return assistant_message


async def call_action_model(
    *,
    messages: list[dict[str, Any]],
    session: Any = None,
    max_tokens: int = 512,
    timeout_s: float = 120.0,
) -> ActionModelResult:
    """Call the configured action model and return `(text_content, token_usage)`."""
    import aiohttp

    url, api_key, model, thinking = _resolve_runtime_config()
    if not api_key:
        raise RuntimeError(
            "LLM_API_KEY (or AGENTIC_QUERY_LLM_KEY / MOD_AGENT_LLM_KEY fallback) is not set."
        )

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": int(max_tokens),
    }
    if thinking in {"enabled", "disabled"}:
        payload["thinking"] = {"type": thinking}
    if thinking != "enabled":
        payload["temperature"] = 0
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
    message = message if isinstance(message, dict) else {}
    content = _extract_message_text(message, first_choice)
    if not content:
        message_keys = sorted(message.keys())
        finish_reason = first_choice.get("finish_reason") if isinstance(first_choice, dict) else None
        has_reasoning = bool(
            str(message.get("reasoning_content") or message.get("reasoning") or "").strip()
        )
        raise RuntimeError(
            "Agentic query LLM returned empty message content "
            f"(finish_reason={finish_reason!r}, message_keys={message_keys!r}, "
            f"has_reasoning_content={has_reasoning!r})."
        )
    return ActionModelResult(
        content=content,
        usage=_extract_usage(data),
        assistant_message=_assistant_message_for_transcript(message, content),
    )
