"""Query refinement via an OpenAI-compatible chat-completions endpoint."""

from __future__ import annotations

import os
from typing import Any

import aiohttp

from app.service.llm_env import (
    resolve_llm_api_key,
    resolve_llm_api_url,
    resolve_llm_model,
    resolve_llm_thinking,
)

_SYSTEM_PROMPT = (
    "You rewrite user queries for retrieval. "
    "Return one short refined search query only. "
    "Do not explain your changes and do not add quotes."
)


def _resolve_runtime_config() -> tuple[str, str | None, str, str]:
    """Resolve endpoint/key/model from canonical env vars with legacy fallbacks."""

    url = resolve_llm_api_url(os.getenv("BEAM_REFINE_LLM_URL"))
    api_key = resolve_llm_api_key(os.getenv("BEAM_REFINE_LLM_KEY"))
    model = resolve_llm_model(default="deepseek-v4-flash")
    thinking = resolve_llm_thinking(url=url, model=model)
    return url, api_key, model, thinking


def _extract_message_text(data: dict[str, Any]) -> str:
    """Extract the first assistant message content from a chat-completions response."""

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    first_choice = choices[0] if isinstance(choices[0], dict) else {}
    message = first_choice.get("message") if isinstance(first_choice, dict) else {}
    if not isinstance(message, dict):
        return ""

    content = message.get("content")
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()

    return ""


async def refine_query(query: str) -> str:
    """Rewrite the user query into a retrieval-friendly search query."""

    url, api_key, model, thinking = _resolve_runtime_config()
    if not api_key:
        raise RuntimeError("LLM_API_KEY (or BEAM_REFINE_LLM_KEY fallback) is not set.")

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": str(query or "")},
        ],
        "max_tokens": 128,
    }
    if thinking in {"enabled", "disabled"}:
        payload["thinking"] = {"type": thinking}
    if thinking != "enabled":
        payload["temperature"] = 0

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ValueError(f"LLM request failed ({resp.status}): {text}")

            data = await resp.json()
            if not isinstance(data, dict):
                raise ValueError("LLM request failed: expected JSON object response.")

    refined_query = _extract_message_text(data)
    if not refined_query:
        raise ValueError("LLM request failed: empty refined query.")
    return refined_query
