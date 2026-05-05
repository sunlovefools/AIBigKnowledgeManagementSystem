"""OpenAI-compatible LLM adapter for the Skills modification runtime."""
from __future__ import annotations

import os
from typing import Any

from app.service.llm_env import (
    resolve_llm_api_key,
    resolve_llm_api_url,
    resolve_llm_model,
    resolve_llm_thinking,
)


def _normalize_url(raw: str) -> str:
    url = str(raw or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    return f"{url}/chat/completions"


def _resolve_runtime_config() -> tuple[str, str | None, str, str]:
    url = resolve_llm_api_url(
        os.getenv("AGENTIC_MODIFICATION_SKILL_LLM_URL"),
        os.getenv("MOD_AGENT_LLM_URL"),
        os.getenv("AGENTIC_QUERY_LLM_URL"),
    )
    api_key = resolve_llm_api_key(
        os.getenv("AGENTIC_MODIFICATION_SKILL_LLM_KEY")
        or os.getenv("MOD_AGENT_LLM_KEY")
        or os.getenv("AGENTIC_QUERY_LLM_KEY")
    )
    model = resolve_llm_model(
        os.getenv("AGENTIC_MODIFICATION_SKILL_LLM_MODEL")
        or os.getenv("MOD_AGENT_LLM_MODEL")
        or os.getenv("AGENTIC_QUERY_LLM_MODEL")
    )
    thinking = resolve_llm_thinking(
        os.getenv("AGENTIC_MODIFICATION_SKILL_THINKING"),
        os.getenv("MOD_AGENT_LLM_THINKING"),
        url=url,
        model=model,
    )
    return url, api_key, model, str(thinking).strip().lower()


def _extract_usage(data: dict[str, Any]) -> dict[str, int]:
    usage = data.get("usage") if isinstance(data, dict) else {}
    if not isinstance(usage, dict):
        usage = {}
    prompt_tokens = int(
        usage.get("prompt_tokens", 0)
        or (
            int(usage.get("prompt_cache_hit_tokens", 0) or 0)
            + int(usage.get("prompt_cache_miss_tokens", 0) or 0)
        )
    )
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)
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


def _extract_message_text(choice: dict[str, Any]) -> str:
    message = choice.get("message") if isinstance(choice, dict) else {}
    candidates: list[Any] = []

    if isinstance(message, dict):
        candidates.extend(
            [
                message.get("content"),
                message.get("text"),
            ]
        )
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if isinstance(function, dict):
                    candidates.append(function.get("arguments"))
    candidates.append(choice.get("text"))

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


async def call_action_model(
    *,
    messages: list[dict[str, Any]],
    session: Any = None,
    max_tokens: int = 1024,
    timeout_s: float = 120.0,
) -> tuple[str, dict[str, int]]:
    import aiohttp

    url, api_key, model, thinking = _resolve_runtime_config()
    if not api_key:
        raise RuntimeError(
            "LLM_API_KEY (or AGENTIC_MODIFICATION_SKILL_LLM_KEY / AGENTIC_QUERY_LLM_KEY / "
            "MOD_AGENT_LLM_KEY fallback) is not set."
        )

    payload = {
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
        async with http_session.post(url, json=payload, headers=headers, timeout=timeout) as response:
            if response.status != 200:
                body = await response.text()
                raise RuntimeError(f"Modification skill LLM API error ({response.status}): {body}")
            data = await response.json()
            if not isinstance(data, dict):
                raise RuntimeError("Modification skill LLM returned a non-JSON object response.")
            return data

    if session is not None:
        data = await _do_request(session)
    else:
        async with aiohttp.ClientSession() as own_session:
            data = await _do_request(own_session)

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Modification skill LLM returned empty choices.")
    first_choice = choices[0] if isinstance(choices[0], dict) else {}
    content = _extract_message_text(first_choice)
    if not content:
        message = first_choice.get("message") if isinstance(first_choice, dict) else {}
        message_keys = sorted(message.keys()) if isinstance(message, dict) else []
        finish_reason = first_choice.get("finish_reason") if isinstance(first_choice, dict) else None
        has_reasoning = bool(
            isinstance(message, dict)
            and str(message.get("reasoning_content") or message.get("reasoning") or "").strip()
        )
        raise RuntimeError(
            "Modification skill LLM returned empty message content "
            f"(finish_reason={finish_reason!r}, message_keys={message_keys!r}, "
            f"has_reasoning_content={has_reasoning!r})."
        )
    return content, _extract_usage(data)
