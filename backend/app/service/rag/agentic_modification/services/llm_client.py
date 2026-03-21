"""LLM service helpers for Agentic Modification."""
from __future__ import annotations

import json
import os
from typing import Any

import aiohttp

from ..shared.logging import (
    log_modification_agent_llm_request,
    log_modification_agent_llm_response,
    log_token_usage,
)
from ..state.retrieval_brief_state import RetrievalBriefState


def _normalize_url(raw: str) -> str:
    """Normalize base URL so it ends with /chat/completions."""
    url = (raw or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    return f"{url}/chat/completions"


_DEEPSEEK_URL = _normalize_url(
    os.getenv("MOD_AGENT_LLM_URL", "https://api.deepseek.com/v1/chat/completions")
)
_DEEPSEEK_KEY = os.getenv("MOD_AGENT_LLM_KEY")
_DEEPSEEK_MODEL = os.getenv("MOD_AGENT_LLM_MODEL", "deepseek-chat")


def calculate_total_cost(
    prompt_cache_hit_tokens: int,
    prompt_cache_miss_tokens: int,
    completion_tokens: int,
) -> float:
    """Estimate call cost from token usage and DeepSeek pricing."""
    cost_per_one_million_prompt_cache_hit_tokens = 0.028
    cost_per_one_million_prompt_cache_miss_tokens = 0.28
    cost_per_one_million_completion_tokens = 0.42

    return (
        (prompt_cache_hit_tokens / 1000000) * cost_per_one_million_prompt_cache_hit_tokens
        + (prompt_cache_miss_tokens / 1000000) * cost_per_one_million_prompt_cache_miss_tokens
        + (completion_tokens / 1000000) * cost_per_one_million_completion_tokens
    )


async def _call_llm(
    system_prompt: str | None = None,
    user_message: str | None = None,
    *,
    messages: list[dict[str, Any]] | None = None,
    session: aiohttp.ClientSession | None = None,
    run_id: str | None = None,
    step: str | None = None,
    max_tokens: int = 512,
) -> tuple[str, dict[str, int]]:
    """Call OpenAI-compatible chat completions endpoint and return text plus usage."""
    if not _DEEPSEEK_KEY:
        raise RuntimeError("MOD_AGENT_LLM_KEY is not set.")

    normalized_messages: list[dict[str, str]] = []
    if isinstance(messages, list):
        for raw_message in messages:
            if not isinstance(raw_message, dict):
                continue
            role = str(raw_message.get("role") or "").strip().lower()
            if role not in {"system", "user", "assistant", "tool"}:
                continue
            normalized_messages.append(
                {
                    "role": role,
                    "content": str(raw_message.get("content") or ""),
                }
            )
        if not normalized_messages:
            raise RuntimeError("messages must include at least one valid chat message.")
    else:
        normalized_messages = [
            {"role": "system", "content": str(system_prompt or "")},
            {"role": "user", "content": str(user_message or "")},
        ]

    payload = {
        "model": _DEEPSEEK_MODEL,
        "messages": normalized_messages,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {_DEEPSEEK_KEY}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=120.0)

    system_prompt_for_log = str(system_prompt or "")
    user_message_for_log = str(user_message or "")
    if isinstance(messages, list):
        system_prompt_for_log = "[message_mode]"
        user_message_for_log = json.dumps(normalized_messages, ensure_ascii=False)

    # Log the LLM request details for observability before making the API call
    log_modification_agent_llm_request(
        provider="MOD_AGENT_LLM",
        model=_DEEPSEEK_MODEL,
        step=step,
        run_id=run_id,
        system_prompt=system_prompt_for_log,
        user_message=user_message_for_log,
    )

    async def _do_request(http_session: aiohttp.ClientSession) -> dict:
        """Internal async function for making the llm API request."""
        async with http_session.post(
            _DEEPSEEK_URL, json=payload, headers=headers, timeout=timeout
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"DeepSeek API error ({resp.status}): {text}")
            return await resp.json()

    # Requesting to the LLM API
    if session is not None:
        data = await _do_request(session)
    else:
        async with aiohttp.ClientSession() as own_session:
            data = await _do_request(own_session)

    usage = data.get("usage") if isinstance(data, dict) else {}
    if not isinstance(usage, dict):
        usage = {}

    # Calculate the token usage and estimated cost, and log it for observability
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    prompt_cache_hit_token = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
    prompt_cache_miss_token = int(usage.get("prompt_cache_miss_tokens", 0) or 0)
    prompt_tokens = prompt_cache_hit_token + prompt_cache_miss_token
    total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)
    estimate_cost = calculate_total_cost(
        prompt_cache_hit_token,
        prompt_cache_miss_token,
        completion_tokens,
    )

    log_token_usage(
        provider="MOD_AGENT_LLM",
        model=_DEEPSEEK_MODEL,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=estimate_cost,
        operation="modification_agent_llm_call",
        run_id=run_id,
        step=step,
    )

    # Extract the responses from the LLM output and log it for observability
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("DeepSeek returned empty choices.")
    content = choices[0].get("message", {}).get("content", "").strip()
    if not content:
        raise RuntimeError("DeepSeek returned empty content.")
    log_modification_agent_llm_response(
        provider="MOD_AGENT_LLM",
        model=_DEEPSEEK_MODEL,
        step=step,
        run_id=run_id,
        response_text=content,
    )

    # Return both the content and the usage details for further processing and accumulation
    # TODO: Not sure why do we need to return the usage details here
    return content, {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _accumulate_usage(
    state: RetrievalBriefState,
    usage: dict[str, int],
) -> dict[str, int]:
    """Merge one LLM usage report into cumulative totals."""
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)
    should_count_call = 1 if (prompt_tokens > 0 or completion_tokens > 0 or total_tokens > 0) else 0

    return {
        "token_prompt_total": int(state.get("token_prompt_total", 0) or 0) + prompt_tokens,
        "token_completion_total": int(state.get("token_completion_total", 0) or 0) + completion_tokens,
        "token_total": int(state.get("token_total", 0) or 0) + total_tokens,
        "llm_call_count": int(state.get("llm_call_count", 0) or 0) + should_count_call,
    }
