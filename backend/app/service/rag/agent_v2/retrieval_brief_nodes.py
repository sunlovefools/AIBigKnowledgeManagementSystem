"""
LangGraph node functions for Agent v2 retrieval brief extraction.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import aiohttp

try:
    from backend.debug.debug_logger import (
        log_token_usage,
        log_modification_agent_llm_request,
        log_modification_agent_llm_response,
    )
except ImportError:
    from debug.debug_logger import (
        log_token_usage,
        log_modification_agent_llm_request,
        log_modification_agent_llm_response,
    )

from .retrieval_brief_prompts import (
    RETRIEVAL_BRIEF_EXTRACTOR_SYSTEM_PROMPT,
    RETRIEVAL_BRIEF_EXTRACTOR_USER_PROMPT,
)
from .retrieval_brief_state import RetrievalBriefState


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
    """
    Estimate call cost from token usage and DeepSeek pricing.
    """
    cost_per_one_million_prompt_cache_hit_tokens = 0.028
    cost_per_one_million_prompt_cache_miss_tokens = 0.28
    cost_per_one_million_completion_tokens = 0.42

    return (
        (prompt_cache_hit_tokens / 1000000) * cost_per_one_million_prompt_cache_hit_tokens
        + (prompt_cache_miss_tokens / 1000000) * cost_per_one_million_prompt_cache_miss_tokens
        + (completion_tokens / 1000000) * cost_per_one_million_completion_tokens
    )


async def _call_llm(
    system_prompt: str,
    user_message: str,
    *,
    session: aiohttp.ClientSession | None = None,
    run_id: str | None = None,
    step: str | None = None,
    max_tokens: int = 512,
) -> tuple[str, dict[str, int]]:
    """Call OpenAI-compatible chat completions endpoint and return text plus usage."""
    if not _DEEPSEEK_KEY:
        raise RuntimeError("MOD_AGENT_LLM_KEY is not set.")

    payload = {
        "model": _DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {_DEEPSEEK_KEY}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=120.0)
    log_modification_agent_llm_request(
        provider="MOD_AGENT_LLM",
        model=_DEEPSEEK_MODEL,
        step=step,
        run_id=run_id,
        system_prompt=system_prompt,
        user_message=user_message,
    )

    async def _do_request(http_session: aiohttp.ClientSession) -> dict:
        async with http_session.post(
            _DEEPSEEK_URL, json=payload, headers=headers, timeout=timeout
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"DeepSeek API error ({resp.status}): {text}")
            return await resp.json()

    if session is not None:
        data = await _do_request(session)
    else:
        async with aiohttp.ClientSession() as own_session:
            data = await _do_request(own_session)

    usage = data.get("usage") if isinstance(data, dict) else {}
    if not isinstance(usage, dict):
        usage = {}

    # Calculate the token costs according to DeepSeek's pricing and log the usage
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
        operation="modification_agent_v2_llm_call",
        run_id=run_id,
        step=step,
    )

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


def _clean_llm_output(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2:
            if lines[-1].strip().startswith("```"):
                cleaned = "\n".join(lines[1:-1]).strip()
            else:
                cleaned = "\n".join(lines[1:]).strip()
    return cleaned


def _parse_json_object(text: str) -> dict[str, Any]:
    """Parse JSON object from model output, recovering from wrapper text if possible."""
    cleaned = _clean_llm_output(text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        snippet = cleaned[start : end + 1]
        parsed = json.loads(snippet)
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("Failed to parse retrieval brief JSON object.")


_STOPWORDS = {
    "a", "an", "and", "all", "any", "as", "at", "be", "by", "for", "from",
    "in", "is", "it", "of", "on", "or", "that", "the", "to", "under",
    "update", "change", "modify", "replace", "remove", "set", "make",
}


def _fallback_goal(user_instruction: str) -> str:
    instruction = re.sub(r"\s+", " ", (user_instruction or "").strip())
    if not instruction:
        return "Update content based on user instruction."
    if len(instruction) > 140:
        instruction = instruction[:137].rstrip() + "..."
    if not instruction.endswith("."):
        instruction += "."
    return instruction


def _fallback_anchors(user_instruction: str) -> list[str]:
    instruction = user_instruction or ""
    anchors: list[str] = []

    quoted_phrases = re.findall(r'"([^"]+)"|\'([^\']+)\'', instruction)
    for pair in quoted_phrases:
        phrase = (pair[0] or pair[1]).strip()
        if phrase:
            anchors.append(phrase)

    numeric_phrases = re.findall(
        r"\b\d+(?:\.\d+)?\s*(?:days?|weeks?|months?|years?|hours?|minutes?|%|percent)\b",
        instruction,
        flags=re.IGNORECASE,
    )
    anchors.extend(numeric_phrases)

    entities = re.findall(r"\b[A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*)*\b", instruction)
    anchors.extend(entities)

    keywords = re.findall(r"\b[a-zA-Z][a-zA-Z0-9-]{2,}\b", instruction.lower())
    for word in keywords:
        if word not in _STOPWORDS:
            anchors.append(word)

    return _normalize_anchors(anchors)[:5]


def _normalize_goal(raw_goal: Any, user_instruction: str) -> str:
    goal = str(raw_goal).strip() if raw_goal is not None else ""
    if not goal:
        goal = _fallback_goal(user_instruction)
    goal = re.sub(r"\s+", " ", goal).strip()
    if len(goal) > 180:
        goal = goal[:177].rstrip() + "..."
    if not goal.endswith("."):
        goal += "."
    return goal


def _normalize_anchors(raw_anchors: Any) -> list[str]:
    if isinstance(raw_anchors, list):
        candidates = raw_anchors
    elif isinstance(raw_anchors, str):
        candidates = [raw_anchors]
    else:
        candidates = []

    normalized: list[str] = []
    seen: set[str] = set()

    for item in candidates:
        if not isinstance(item, str):
            continue
        anchor = re.sub(r"\s+", " ", item).strip().strip(",.;:")
        if not anchor:
            continue
        key = anchor.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(anchor)

    return normalized


def _normalize_constraint(raw_constraint: Any) -> str:
    constraint = str(raw_constraint).strip() if raw_constraint is not None else ""
    if not constraint:
        return "None"
    lowered = constraint.casefold()
    if lowered in {"none", "null", "n/a", "na", "no constraint"}:
        return "None"
    constraint = re.sub(r"\s+", " ", constraint).strip()
    return constraint if constraint else "None"


async def retrieval_brief_extractor_node(state: RetrievalBriefState) -> dict:
    """Extract retrieval brief (goal, anchors, constraint) from user instruction."""
    print("[Agent v2 - Node 1] Extracting retrieval brief...")
    user_instruction = state.get("user_instructions", "")

    fallback = {
        "goal": _fallback_goal(user_instruction),
        "anchors": _fallback_anchors(user_instruction),
        "constraint": "None",
    }
    if not fallback["anchors"]:
        fallback["anchors"] = ["document"]

    try:
        llm_text, usage = await _call_llm(
            system_prompt=RETRIEVAL_BRIEF_EXTRACTOR_SYSTEM_PROMPT,
            user_message=RETRIEVAL_BRIEF_EXTRACTOR_USER_PROMPT.format(
                user_instruction=user_instruction
            ),
            session=state.get("_session"),
            run_id=state.get("run_id"),
            step="retrieval_brief_extractor",
            max_tokens=512,
        )
        parsed = _parse_json_object(llm_text)

        goal = _normalize_goal(parsed.get("goal"), user_instruction)
        anchors = _normalize_anchors(parsed.get("anchors"))
        if not anchors:
            anchors = fallback["anchors"]
        constraint = _normalize_constraint(parsed.get("constraint"))

        return {
            "goal": goal,
            "anchors": anchors,
            "constraint": constraint,
            **_accumulate_usage(state, usage),
        }
    except Exception as error:
        print(f"Retrieval brief extraction failed: {error}. Falling back.")
        return fallback
