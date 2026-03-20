"""
LangGraph node functions for Agent v2 retrieval brief extraction and search/group.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from statistics import mean
from typing import Any

import aiohttp
from langchain_core.documents import Document

try:
    from backend.debug.debug_logger import (
        log_token_usage,
        log_modification_agent_llm_request,
        log_modification_agent_llm_response,
        log_modification_agent_search_group,
    )
except ImportError:
    from debug.debug_logger import (
        log_token_usage,
        log_modification_agent_llm_request,
        log_modification_agent_llm_response,
        log_modification_agent_search_group,
    )

from .retrieval_brief_prompts import (
    RETRIEVAL_BRIEF_EXTRACTOR_SYSTEM_PROMPT,
    RETRIEVAL_BRIEF_EXTRACTOR_USER_PROMPT,
)
from .retrieval_brief_state import RetrievalBriefState


SEARCH_TOP_K = 15


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
        """Internal async function for making the llm API request."""
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
        r"\b\d+(?:\.\d+)?\s*(?:days?|weeks?|months?|years?|hours?|minutes?|%|percent|usd|eur|gbp|dollars?)\b",
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


def _fallback_semantic_anchors(user_instruction: str) -> list[str]:
    return _fallback_anchors(user_instruction)


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


def _combine_anchors(lexical_anchors: list[str], semantic_anchors: list[str]) -> list[str]:
    return _normalize_anchors([*lexical_anchors, *semantic_anchors])


def _normalize_constraint(raw_constraint: Any) -> str:
    constraint = str(raw_constraint).strip() if raw_constraint is not None else ""
    if not constraint:
        return "None"
    lowered = constraint.casefold()
    if lowered in {"none", "null", "n/a", "na", "no constraint"}:
        return "None"
    constraint = re.sub(r"\s+", " ", constraint).strip()
    return constraint if constraint else "None"


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _average_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(mean(values)), 6)


def _extract_file_metadata(metadata: dict[str, Any]) -> tuple[str, str]:
    file_metadata = metadata.get("file_metadata")
    if not isinstance(file_metadata, dict):
        file_metadata = {}

    file_id = str(
        file_metadata.get("file_id")
        or metadata.get("file_id")
        or "unknown"
    ).strip() or "unknown"
    file_name = str(
        file_metadata.get("file_name")
        or metadata.get("file_name")
        or "unknown"
    ).strip() or "unknown"

    return file_id, file_name


def _resolve_lexical_child_chunk_id(row: dict[str, Any], query: str) -> str:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    child_metadata = metadata.get("child_chunk_metadata")
    if not isinstance(child_metadata, dict):
        child_metadata = {}

    child_chunk_id = (
        row.get("_id")
        or metadata.get("child_chunk_id")
        or child_metadata.get("child_chunk_id")
    )

    child_chunk_id_str = str(child_chunk_id or "").strip()
    if not child_chunk_id_str:
        raise ValueError(f"Missing child_chunk_id in lexical hit for query={query!r}.")

    return child_chunk_id_str


def _resolve_semantic_child_chunk_id(doc: Document, query: str) -> str:
    metadata = doc.metadata if isinstance(doc.metadata, dict) else {}
    child_metadata = metadata.get("child_chunk_metadata")
    if not isinstance(child_metadata, dict):
        child_metadata = {}

    child_chunk_id = (
        getattr(doc, "id", None)
        or metadata.get("child_chunk_id")
        or metadata.get("_id")
        or metadata.get("id")
        or child_metadata.get("child_chunk_id")
    )

    child_chunk_id_str = str(child_chunk_id or "").strip()
    if not child_chunk_id_str:
        raise ValueError(f"Missing child_chunk_id in semantic hit for query={query!r}.")

    return child_chunk_id_str


async def _run_lexical_search(query: str, top_k: int) -> list[dict[str, Any]]:
    from app.vectordb.vectordb import lexical_search_child_chunks

    return await lexical_search_child_chunks(query=query, top_k=top_k)


async def _run_semantic_search(query: str, top_k: int) -> list[tuple[Document, float]]:
    from app.vectordb import vectordb as vectordb_module

    return await vectordb_module.VECTOR_STORE.asimilarity_search_with_score(query, k=top_k)


async def retrieval_brief_extractor_node(state: RetrievalBriefState) -> dict:
    """Extract retrieval brief (goal, split anchors, constraint) from user instruction."""
    print("[Agent v2 - Node 1] Extracting retrieval brief...")
    user_instruction = state.get("user_instructions", "")

    fallback_lexical_anchors = _fallback_anchors(user_instruction)
    if not fallback_lexical_anchors:
        fallback_lexical_anchors = ["document"]

    fallback_semantic_anchors = _fallback_semantic_anchors(user_instruction)
    if not fallback_semantic_anchors:
        fallback_semantic_anchors = fallback_lexical_anchors[:]

    fallback = {
        "goal": _fallback_goal(user_instruction),
        "lexical_anchors": fallback_lexical_anchors,
        "semantic_anchors": fallback_semantic_anchors,
        "anchors": _combine_anchors(fallback_lexical_anchors, fallback_semantic_anchors),
        "constraint": "None",
    }

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

        lexical_anchors = _normalize_anchors(parsed.get("lexical_anchors"))
        semantic_anchors = _normalize_anchors(parsed.get("semantic_anchors"))

        # Backward compatibility for any legacy prompt output that still uses `anchors`.
        legacy_anchors = _normalize_anchors(parsed.get("anchors"))
        if not lexical_anchors and legacy_anchors:
            lexical_anchors = legacy_anchors
        if not semantic_anchors and legacy_anchors:
            semantic_anchors = legacy_anchors

        if not lexical_anchors:
            lexical_anchors = fallback_lexical_anchors
        if not semantic_anchors:
            semantic_anchors = fallback_semantic_anchors

        anchors = _combine_anchors(lexical_anchors, semantic_anchors)
        constraint = _normalize_constraint(parsed.get("constraint"))

        return {
            "goal": goal,
            "lexical_anchors": lexical_anchors,
            "semantic_anchors": semantic_anchors,
            "anchors": anchors,
            "constraint": constraint,
            **_accumulate_usage(state, usage),
        }
    except Exception as error:
        print(f"Retrieval brief extraction failed: {error}. Falling back.")
        return fallback


async def search_and_group_node(state: RetrievalBriefState) -> dict:
    """
    Node 2: run lexical + semantic search from split anchors, then group and flag strong signals.
    """
    print("[Agent v2 - Node 2] Running search and grouping...")
    run_id = state.get("run_id")

    lexical_anchors = _normalize_anchors(state.get("lexical_anchors"))
    semantic_anchors = _normalize_anchors(state.get("semantic_anchors"))
    if not lexical_anchors and not semantic_anchors:
        legacy_anchors = _normalize_anchors(state.get("anchors"))
        lexical_anchors = legacy_anchors
        semantic_anchors = legacy_anchors

    try:
        async def _run_lexical_query(anchor: str) -> tuple[str, list[dict[str, Any]]]:
            """
            Internal function to run lexical search for one anchor and extract relevant metadata and scores.
            """
            rows = await _run_lexical_search(query=anchor, top_k=SEARCH_TOP_K)
            hits: list[dict[str, Any]] = []

            for row in rows:
                metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                child_chunk_id = _resolve_lexical_child_chunk_id(row, query=anchor)
                file_id, file_name = _extract_file_metadata(metadata)

                hits.append(
                    {
                        "source": "lexical",
                        "query": anchor,
                        "child_chunk_id": child_chunk_id,
                        "file_id": file_id,
                        "file_name": file_name,
                    }
                )

            return anchor, hits

        async def _run_semantic_query(anchor: str) -> tuple[str, list[dict[str, Any]]]:
            """
            Internal function to run semantic search for one anchor and extract relevant metadata and scores.
            """
            items = await _run_semantic_search(query=anchor, top_k=SEARCH_TOP_K)
            hits: list[dict[str, Any]] = []

            for item in items:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue

                doc_candidate = item[0]
                if not isinstance(doc_candidate, Document):
                    continue

                child_chunk_id = _resolve_semantic_child_chunk_id(doc_candidate, query=anchor)
                metadata = doc_candidate.metadata if isinstance(doc_candidate.metadata, dict) else {}
                file_id, file_name = _extract_file_metadata(metadata)

                hits.append(
                    {
                        "source": "semantic",
                        "query": anchor,
                        "child_chunk_id": child_chunk_id,
                        "file_id": file_id,
                        "file_name": file_name,
                        "score": _safe_float(item[1]),
                    }
                )

            return anchor, hits

        lexical_results = await asyncio.gather(*[_run_lexical_query(anchor) for anchor in lexical_anchors])
        semantic_results = await asyncio.gather(*[_run_semantic_query(anchor) for anchor in semantic_anchors])

        lexical_hits_by_query = {anchor: hits for anchor, hits in lexical_results}
        semantic_hits_by_query = {anchor: hits for anchor, hits in semantic_results}

        all_hits: list[dict[str, Any]] = []
        for hits in lexical_hits_by_query.values():
            all_hits.extend(hits)
        for hits in semantic_hits_by_query.values():
            all_hits.extend(hits)

        child_agg: dict[str, dict[str, Any]] = {}
        file_agg: dict[str, dict[str, Any]] = {}

        for hit in all_hits:
            child_chunk_id = hit["child_chunk_id"]
            file_id = hit["file_id"]
            file_name = hit["file_name"]
            score = hit.get("score")
            source = hit["source"]

            child_entry = child_agg.setdefault(
                child_chunk_id,
                {
                    "child_chunk_id": child_chunk_id,
                    "file_id": file_id,
                    "file_name": file_name,
                    "lexical_hit_count": 0,
                    "semantic_hit_count": 0,
                    "total_hit_count": 0,
                    "semantic_scores": [],
                },
            )

            child_entry["total_hit_count"] += 1
            if source == "lexical":
                child_entry["lexical_hit_count"] += 1
            else:
                child_entry["semantic_hit_count"] += 1
                if isinstance(score, float):
                    child_entry["semantic_scores"].append(score)

            file_key = f"{file_id}::{file_name}"
            file_entry = file_agg.setdefault(
                file_key,
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "lexical_hit_count": 0,
                    "semantic_hit_count": 0,
                    "total_hit_count": 0,
                    "semantic_scores": [],
                },
            )

            file_entry["total_hit_count"] += 1
            if source == "lexical":
                file_entry["lexical_hit_count"] += 1
            else:
                file_entry["semantic_hit_count"] += 1
                if isinstance(score, float):
                    file_entry["semantic_scores"].append(score)

        child_results: list[dict[str, Any]] = []
        for child_entry in child_agg.values():
            avg_semantic_score = _average_or_none(child_entry["semantic_scores"])
            strong_signal_chunk = (
                child_entry["lexical_hit_count"] > 0 and child_entry["semantic_hit_count"] > 0
            )
            child_results.append(
                {
                    "child_chunk_id": child_entry["child_chunk_id"],
                    "file_id": child_entry["file_id"],
                    "file_name": child_entry["file_name"],
                    "lexical_hit_count": child_entry["lexical_hit_count"],
                    "semantic_hit_count": child_entry["semantic_hit_count"],
                    "total_hit_count": child_entry["total_hit_count"],
                    "avg_semantic_score": avg_semantic_score,
                    "strong_signal_chunk": strong_signal_chunk,
                }
            )

        child_results.sort(
            key=lambda item: (
                -int(item.get("strong_signal_chunk", False)),
                -int(item.get("total_hit_count", 0)),
                str(item.get("child_chunk_id", "")),
            )
        )

        file_results: list[dict[str, Any]] = []
        files_with_high_signal_chunks = {
            f"{item['file_id']}::{item['file_name']}"
            for item in child_results
            if item["strong_signal_chunk"]
        }
        for file_entry in file_agg.values():
            avg_semantic_score = _average_or_none(file_entry["semantic_scores"])

            has_both_sources = (
                file_entry["lexical_hit_count"] > 0 and file_entry["semantic_hit_count"] > 0
            )
            file_key = f"{file_entry['file_id']}::{file_entry['file_name']}"

            # Rule 1: file has at least one lexical and one semantic hit.
            # Rule 2: any high-signal chunk auto-promotes its associated file.
            strong_signal_file = has_both_sources or file_key in files_with_high_signal_chunks

            file_results.append(
                {
                    "file_id": file_entry["file_id"],
                    "file_name": file_entry["file_name"],
                    "lexical_hit_count": file_entry["lexical_hit_count"],
                    "semantic_hit_count": file_entry["semantic_hit_count"],
                    "total_hit_count": file_entry["total_hit_count"],
                    "avg_semantic_score": avg_semantic_score,
                    "strong_signal_file": strong_signal_file,
                }
            )

        file_results.sort(
            key=lambda item: (
                -int(item.get("strong_signal_file", False)),
                -int(item.get("total_hit_count", 0)),
                str(item.get("file_name", "")),
            )
        )

        strong_signal_chunk_refs = [
            {
                "child_chunk_id": item["child_chunk_id"],
                "file_id": item["file_id"],
                "file_name": item["file_name"],
            }
            for item in child_results
            if item["strong_signal_chunk"]
        ]
        strong_signal_file_refs = [
            {
                "file_id": item["file_id"],
                "file_name": item["file_name"],
            }
            for item in file_results
            if item["strong_signal_file"]
        ]

        node_result = {
            "queries": {
                "lexical_anchors": lexical_anchors,
                "semantic_anchors": semantic_anchors,
            },
            "query_hits": {
                "lexical": lexical_hits_by_query,
                "semantic": semantic_hits_by_query,
            },
            "children": child_results,
            "files": file_results,
            "run_summary": {
                "top_k_per_query": SEARCH_TOP_K,
                "lexical_anchor_count": len(lexical_anchors),
                "semantic_anchor_count": len(semantic_anchors),
                "total_lexical_hits": sum(len(hits) for hits in lexical_hits_by_query.values()),
                "total_semantic_hits": sum(len(hits) for hits in semantic_hits_by_query.values()),
                "total_hits": len(all_hits),
                "total_child_chunks": len(child_results),
                "total_files": len(file_results),
                "strong_signal_chunk_count": sum(
                    1 for item in child_results if item["strong_signal_chunk"]
                ),
                "strong_signal_file_count": sum(
                    1 for item in file_results if item["strong_signal_file"]
                ),
                "strong_signal_chunks": strong_signal_chunk_refs if strong_signal_chunk_refs else "none",
                "strong_signal_files": strong_signal_file_refs if strong_signal_file_refs else "none",
            },
        }

        log_modification_agent_search_group(
            run_id=run_id,
            step="search_and_group",
            payload=node_result,
        )

        return {
            "node2_search_group_result": node_result,
        }
    except Exception as error:
        error_message = f"search_and_group node failed: {error}"
        print(error_message)
        log_modification_agent_search_group(
            run_id=run_id,
            step="search_and_group",
            payload={
                "queries": {
                    "lexical_anchors": lexical_anchors,
                    "semantic_anchors": semantic_anchors,
                },
                "error": error_message,
            },
        )
        return {
            "error": error_message,
        }
