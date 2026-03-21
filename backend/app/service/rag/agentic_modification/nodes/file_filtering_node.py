"""Node 4: classify parent chunks into confirmed and potential edit targets."""
from __future__ import annotations

import asyncio
from typing import Any

from ..prompts.retrieval_brief_prompts import (
    FILE_FILTERING_SYSTEM_PROMPT,
    FILE_FILTERING_USER_PROMPT,
)
from ..services import llm_client, vector_search
from ..shared.logging import log_modification_agent_search_group
from ..shared.normalization import (
    _normalize_anchors,
    _normalize_constraint,
    _normalize_goal,
    _parse_json_object,
)
from ..shared.search_utils import (
    _normalize_file_filter_result,
    _build_parent_chunks_prompt_payload,
)
from ..state.retrieval_brief_state import RetrievalBriefState


def _file_key(file_id: str, file_name: str) -> str:
    return f"{file_id}::{file_name}"


def _extract_available_chunk_numbers(parent_chunks: list[dict[str, Any]]) -> set[int]:
    return {
        chunk_number
        for chunk_number in (
            parent_chunk.get("chunk_number")
            for parent_chunk in parent_chunks
            if isinstance(parent_chunk, dict)
        )
        if isinstance(chunk_number, int)
    }


def _normalize_child_chunk_ids(raw_ids: Any) -> list[str]:
    if not isinstance(raw_ids, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_ids:
        child_id = str(item or "").strip()
        if not child_id or child_id in seen:
            continue
        seen.add(child_id)
        normalized.append(child_id)
    return normalized


def _build_filter_candidates(
    *,
    node2_files: list[dict[str, Any]],
    node3_files: list[dict[str, Any]],
    fallback_semantic_anchors: list[str],
) -> list[dict[str, Any]]:
    node3_by_key: dict[str, dict[str, Any]] = {}
    for file_item in node3_files:
        if not isinstance(file_item, dict):
            continue
        file_id = str(file_item.get("file_id") or "").strip() or "unknown"
        file_name = str(file_item.get("file_name") or "").strip() or "unknown"
        node3_by_key[_file_key(file_id, file_name)] = file_item

    candidates: list[dict[str, Any]] = []
    if node2_files:
        for node2_item in node2_files:
            if not isinstance(node2_item, dict):
                continue
            file_id = str(node2_item.get("file_id") or "").strip() or "unknown"
            file_name = str(node2_item.get("file_name") or "").strip() or "unknown"
            key = _file_key(file_id, file_name)
            base = node3_by_key.get(key, {})
            parent_chunks = base.get("parent_chunks", []) if isinstance(base, dict) else []
            semantic_anchors = (
                _normalize_anchors(base.get("semantic_anchors")) if isinstance(base, dict) else []
            )
            if not semantic_anchors:
                semantic_anchors = fallback_semantic_anchors
            candidates.append(
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "strong_signal_file": bool(node2_item.get("strong_signal_file", False)),
                    "parent_chunks": parent_chunks if isinstance(parent_chunks, list) else [],
                    "semantic_anchors": semantic_anchors,
                }
            )
        return candidates

    for node3_item in node3_files:
        if not isinstance(node3_item, dict):
            continue
        file_id = str(node3_item.get("file_id") or "").strip() or "unknown"
        file_name = str(node3_item.get("file_name") or "").strip() or "unknown"
        semantic_anchors = _normalize_anchors(node3_item.get("semantic_anchors"))
        if not semantic_anchors:
            semantic_anchors = fallback_semantic_anchors
        candidates.append(
            {
                "file_id": file_id,
                "file_name": file_name,
                "strong_signal_file": False,
                "parent_chunks": (
                    node3_item.get("parent_chunks", [])
                    if isinstance(node3_item.get("parent_chunks"), list)
                    else []
                ),
                "semantic_anchors": semantic_anchors,
            }
        )
    return candidates


def _build_parent_chunk_refs(file_id: str, file_name: str, chunk_numbers: list[int]) -> list[dict[str, Any]]:
    return [
        {
            "file_id": file_id,
            "file_name": file_name,
            "parent_chunk_number": number,
        }
        for number in chunk_numbers
    ]


def _dedupe_parent_chunk_refs(raw_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped_by_key: dict[str, dict[str, Any]] = {}
    for ref in raw_refs:
        if not isinstance(ref, dict):
            continue
        file_id = str(ref.get("file_id") or "").strip()
        file_name = str(ref.get("file_name") or "").strip() or "unknown"
        parent_chunk_number = ref.get("parent_chunk_number")
        if not file_id or not isinstance(parent_chunk_number, int):
            continue
        deduped_by_key.setdefault(
            f"{file_id}::{parent_chunk_number}",
            {
                "file_id": file_id,
                "file_name": file_name,
                "parent_chunk_number": parent_chunk_number,
            },
        )
    deduped = list(deduped_by_key.values())
    deduped.sort(
        key=lambda item: (
            str(item.get("file_name") or ""),
            str(item.get("file_id") or ""),
            int(item.get("parent_chunk_number", 10**9)),
        )
    )
    return deduped


def _merge_child_exclusion_maps(raw_maps: list[dict[str, list[str]]]) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {}
    for raw_map in raw_maps:
        if not isinstance(raw_map, dict):
            continue
        for file_id, child_ids in raw_map.items():
            normalized_file_id = str(file_id or "").strip()
            if not normalized_file_id:
                continue
            normalized_ids = _normalize_child_chunk_ids(child_ids)
            if not normalized_ids:
                continue
            merged.setdefault(normalized_file_id, set()).update(normalized_ids)
    return merged


def _extract_selected_child_chunk_ids(
    *,
    parent_chunks: list[dict[str, Any]],
    selected_chunk_numbers: set[int],
) -> list[str]:
    if not selected_chunk_numbers:
        return []
    selected_child_ids: list[str] = []
    seen: set[str] = set()
    for parent_chunk in parent_chunks:
        if not isinstance(parent_chunk, dict):
            continue
        chunk_number = parent_chunk.get("chunk_number")
        if not isinstance(chunk_number, int) or chunk_number not in selected_chunk_numbers:
            continue
        child_ids = _normalize_child_chunk_ids(parent_chunk.get("child_chunk_ids"))
        for child_id in child_ids:
            if child_id in seen:
                continue
            seen.add(child_id)
            selected_child_ids.append(child_id)
    return selected_child_ids


async def _evaluate_filter_candidate(
    *,
    file_item: dict[str, Any],
    candidate_index: int,
    goal: str,
    constraint: str,
    retrieval_cache: dict[str, Any],
    session: Any,
    run_id: str | None,
) -> dict[str, Any]:
    file_id = str(file_item.get("file_id") or "unknown")
    file_name = str(file_item.get("file_name") or "unknown")
    parent_chunks = [
        dict(parent_chunk)
        for parent_chunk in file_item.get("parent_chunks", [])
        if isinstance(parent_chunk, dict)
    ]
    semantic_anchors = _normalize_anchors(file_item.get("semantic_anchors"))
    available_chunk_numbers = _extract_available_chunk_numbers(parent_chunks)

    usage_totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    llm_calls_made = 0

    try:
        llm_text, usage = await llm_client._call_llm(
            system_prompt=FILE_FILTERING_SYSTEM_PROMPT,
            user_message=FILE_FILTERING_USER_PROMPT.format(
                goal=goal,
                constraint=constraint,
                parent_chunks=_build_parent_chunks_prompt_payload(parent_chunks),
            ),
            session=session,
            run_id=run_id,
            step="file_filtering",
            max_tokens=512,
        )
        llm_calls_made = 1
        usage_totals["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
        usage_totals["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)
        usage_totals["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
        parsed = _parse_json_object(llm_text)
        final_normalized = _normalize_file_filter_result(
            parsed,
            available_chunk_numbers=available_chunk_numbers,
            fallback_reason="Model response normalized.",
        )
    except Exception as error:
        final_normalized = _normalize_file_filter_result(
            {},
            available_chunk_numbers=available_chunk_numbers,
            fallback_reason=f"Fallback empty classification due to file filtering error: {error}",
        )

    confirmed_chunk_numbers = final_normalized["confirmed_parent_chunks"]
    potential_chunk_numbers = final_normalized["potential_parent_chunks"]

    selected_child_chunk_ids = _extract_selected_child_chunk_ids(
        parent_chunks=parent_chunks,
        selected_chunk_numbers=set(confirmed_chunk_numbers) | set(potential_chunk_numbers),
    )

    evaluation = {
        "file_id": file_id,
        "file_name": file_name,
        "parent_chunk_count": len(parent_chunks),
        "semantic_anchor_count": len(semantic_anchors),
        "reasoning_summary": final_normalized["reasoning_summary"],
        "confirmed_parent_chunks": confirmed_chunk_numbers,
        "potential_parent_chunks": potential_chunk_numbers,
        "strong_signal_file": bool(file_item.get("strong_signal_file", False)),
        "excluded_child_chunk_ids": selected_child_chunk_ids,
    }

    confirmed_refs = _build_parent_chunk_refs(file_id, file_name, confirmed_chunk_numbers)
    potential_refs = _build_parent_chunk_refs(file_id, file_name, potential_chunk_numbers)
    excluded_child_chunk_ids_by_file = {file_id: selected_child_chunk_ids} if selected_child_chunk_ids else {}

    return {
        "candidate_index": candidate_index,
        "evaluation": evaluation,
        "confirmed_refs": confirmed_refs,
        "potential_refs": potential_refs,
        "potential_file_id": file_id if potential_chunk_numbers else "",
        "excluded_child_chunk_ids_by_file": excluded_child_chunk_ids_by_file,
        "usage_totals": usage_totals,
        "llm_calls_made": llm_calls_made,
    }


async def run_file_filtering_batch(
    state: RetrievalBriefState,
    *,
    search_group_result: dict[str, Any],
    expansion_result: dict[str, Any],
    batch_id: int | None = None,
) -> tuple[dict[str, Any], dict[str, int], int]:
    """
    Reusable file-filtering batch runner.
    Returns:
      (node_result, usage_totals, llm_calls_made)
    """
    node2_files_raw = search_group_result.get("files") if isinstance(search_group_result, dict) else []
    node2_files = node2_files_raw if isinstance(node2_files_raw, list) else []

    node3_files_raw = expansion_result.get("files") if isinstance(expansion_result, dict) else []
    node3_files = node3_files_raw if isinstance(node3_files_raw, list) else []

    fallback_semantic_anchors = _normalize_anchors(
        (expansion_result.get("queries", {}) if isinstance(expansion_result, dict) else {}).get("semantic_anchors")
    )
    if not fallback_semantic_anchors:
        fallback_semantic_anchors = _normalize_anchors(state.get("semantic_anchors"))

    filter_candidates = _build_filter_candidates(
        node2_files=[item for item in node2_files if isinstance(item, dict)],
        node3_files=[item for item in node3_files if isinstance(item, dict)],
        fallback_semantic_anchors=fallback_semantic_anchors,
    )

    strong_signal_files = [
        {
            "file_id": str(file_item.get("file_id") or "unknown"),
            "file_name": str(file_item.get("file_name") or "unknown"),
            "reason": "strong_signal_file",
        }
        for file_item in filter_candidates
        if bool(file_item.get("strong_signal_file", False))
    ]

    goal = _normalize_goal(state.get("goal"), state.get("user_instructions", ""))
    constraint = _normalize_constraint(state.get("constraint"))
    resolved_batch_id = int(batch_id or 1)

    retrieval_cache = vector_search._ensure_retrieval_cache(state.get("_retrieval_cache"))
    state["_retrieval_cache"] = retrieval_cache

    usage_totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    llm_calls_made = 0
    file_evaluations: list[dict[str, Any]] = []
    confirmed_refs_all: list[dict[str, Any]] = []
    potential_refs_all: list[dict[str, Any]] = []
    potential_file_ids: set[str] = set()
    child_exclusion_maps: list[dict[str, list[str]]] = []
    run_id = state.get("run_id")

    evaluation_tasks = [
        asyncio.create_task(
            _evaluate_filter_candidate(
                file_item=file_item,
                candidate_index=index,
                goal=goal,
                constraint=constraint,
                retrieval_cache=retrieval_cache,
                session=state.get("_session"),
                run_id=run_id,
            )
        )
        for index, file_item in enumerate(filter_candidates)
    ]
    evaluation_results = await asyncio.gather(*evaluation_tasks) if evaluation_tasks else []
    evaluation_results.sort(key=lambda item: int(item.get("candidate_index", 0) or 0))

    for item in evaluation_results:
        usage = item.get("usage_totals", {})
        if isinstance(usage, dict):
            usage_totals["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
            usage_totals["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)
            usage_totals["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
        llm_calls_made += int(item.get("llm_calls_made", 0) or 0)

        evaluation = item.get("evaluation")
        if isinstance(evaluation, dict):
            file_evaluations.append(evaluation)

        confirmed_refs = item.get("confirmed_refs")
        if isinstance(confirmed_refs, list):
            confirmed_refs_all.extend(confirmed_refs)

        potential_refs = item.get("potential_refs")
        if isinstance(potential_refs, list):
            potential_refs_all.extend(potential_refs)

        potential_file_id = str(item.get("potential_file_id") or "").strip()
        if potential_file_id:
            potential_file_ids.add(potential_file_id)

        child_exclusion_map = item.get("excluded_child_chunk_ids_by_file")
        if isinstance(child_exclusion_map, dict):
            child_exclusion_maps.append(child_exclusion_map)

    merged_confirmed_refs = _dedupe_parent_chunk_refs(confirmed_refs_all)
    merged_potential_refs = _dedupe_parent_chunk_refs(potential_refs_all)
    merged_child_exclusions = _merge_child_exclusion_maps(child_exclusion_maps)
    excluded_child_chunk_ids_by_file = [
        {
            "file_id": file_id,
            "child_chunk_ids": sorted(child_ids),
        }
        for file_id, child_ids in sorted(merged_child_exclusions.items())
        if child_ids
    ]

    node_result = {
        "batch_id": resolved_batch_id,
        "goal": goal,
        "constraint": constraint,
        "strong_signal_files": strong_signal_files,
        "evaluations": file_evaluations,
        "merged_confirmed_parent_chunk_refs": merged_confirmed_refs,
        "merged_potential_parent_chunk_refs": merged_potential_refs,
        "potential_file_ids": sorted(potential_file_ids),
        "excluded_child_chunk_ids_by_file": excluded_child_chunk_ids_by_file,
        "run_summary": {
            "strong_signal_file_count": len(strong_signal_files),
            "evaluated_file_count": len(file_evaluations),
            "confirmed_file_count": len(
                {
                    str(ref.get("file_id") or "").strip()
                    for ref in merged_confirmed_refs
                    if isinstance(ref, dict) and str(ref.get("file_id") or "").strip()
                }
            ),
            "potential_file_count": len(potential_file_ids),
            "confirmed_parent_chunk_ref_count": len(merged_confirmed_refs),
            "potential_parent_chunk_ref_count": len(merged_potential_refs),
            "llm_call_count": llm_calls_made,
            "cache_stats": vector_search._snapshot_retrieval_cache_stats(retrieval_cache),
        },
    }
    return node_result, usage_totals, llm_calls_made


async def file_filtering_node(state: RetrievalBriefState) -> dict:
    """Node 4 wrapper: run one file-filtering batch."""
    print("[Agentic Modification - Node 4] Running file filtering...")
    run_id = state.get("run_id")
    node2_result = state.get("node2_search_group_result", {})
    node3_result = state.get("node3_non_strong_signal_file_context_expansion_result", {})
    batch_id = int(node3_result.get("batch_id", 1)) if isinstance(node3_result, dict) else 1
    goal = _normalize_goal(state.get("goal"), state.get("user_instructions", ""))
    constraint = _normalize_constraint(state.get("constraint"))

    retrieval_cache = vector_search._ensure_retrieval_cache(state.get("_retrieval_cache"))
    state["_retrieval_cache"] = retrieval_cache

    try:
        node_result, usage_totals, llm_calls_made = await run_file_filtering_batch(
            state,
            search_group_result=node2_result if isinstance(node2_result, dict) else {},
            expansion_result=node3_result if isinstance(node3_result, dict) else {},
            batch_id=batch_id,
        )
        log_modification_agent_search_group(
            run_id=run_id,
            step="file_filtering",
            payload=node_result,
        )
        return {
            "node4_file_filtering_result": node_result,
            "token_prompt_total": int(state.get("token_prompt_total", 0) or 0) + usage_totals["prompt_tokens"],
            "token_completion_total": int(state.get("token_completion_total", 0) or 0) + usage_totals["completion_tokens"],
            "token_total": int(state.get("token_total", 0) or 0) + usage_totals["total_tokens"],
            "llm_call_count": int(state.get("llm_call_count", 0) or 0) + llm_calls_made,
            "_retrieval_cache": retrieval_cache,
        }
    except Exception as error:
        error_message = f"file_filtering node failed: {error}"
        print(error_message)
        log_modification_agent_search_group(
            run_id=run_id,
            step="file_filtering",
            payload={
                "goal": goal,
                "constraint": constraint,
                "error": error_message,
            },
        )
        return {
            "error": error_message,
            "_retrieval_cache": retrieval_cache,
        }
