"""Node 4: iterative file filtering with in-file semantic expansion."""
from __future__ import annotations

from typing import Any

from langchain_core.documents import Document

from ..prompts.retrieval_brief_prompts import (
    FILE_FILTERING_SYSTEM_PROMPT,
    FILE_FILTERING_USER_PROMPT,
)
from ..services import llm_client, vector_search
from ..shared.constants import NON_STRONG_SIGNAL_FILE_TOP_K, POTENTIAL_MATCH_PROMOTION_THRESHOLD
from ..shared.logging import log_modification_agent_search_group
from ..shared.normalization import (
    _normalize_anchors,
    _normalize_constraint,
    _normalize_goal,
    _parse_json_object,
)
from ..shared.search_utils import (
    _average_or_none,
    _build_parent_chunks_prompt_payload,
    _extract_file_metadata,
    _extract_parent_chunk_number,
    _normalize_file_filter_result,
    _parent_sort_key,
    _resolve_semantic_child_chunk_id,
    _resolve_semantic_parent_id,
    _safe_float,
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


async def _expand_file_context_one_round(
    *,
    file_id: str,
    file_name: str,
    semantic_anchors: list[str],
    seen_child_chunk_ids: set[str],
    seen_parent_ids: set[str],
    retrieval_cache: dict[str, Any],
) -> dict[str, Any]:
    semantic_query_hits: dict[str, list[dict[str, Any]]] = {}
    semantic_query_modes: dict[str, str] = {}
    child_aggregate: dict[str, dict[str, Any]] = {}

    for anchor in semantic_anchors:
        try:
            items, search_mode = await vector_search._run_semantic_search_for_file(
                query=anchor,
                file_id=file_id,
                top_k=NON_STRONG_SIGNAL_FILE_TOP_K,
                excluded_child_chunk_ids=seen_child_chunk_ids,
                cache=retrieval_cache,
            )
        except TypeError:
            items, search_mode = await vector_search._run_semantic_search_for_file(
                query=anchor,
                file_id=file_id,
                top_k=NON_STRONG_SIGNAL_FILE_TOP_K,
            )
        semantic_query_modes[anchor] = search_mode
        anchor_hits: list[dict[str, Any]] = []

        for item in items:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            doc_candidate = item[0]
            if not isinstance(doc_candidate, Document):
                continue

            try:
                child_chunk_id = _resolve_semantic_child_chunk_id(doc_candidate, query=anchor)
            except ValueError:
                continue
            if child_chunk_id in seen_child_chunk_ids:
                continue

            metadata = doc_candidate.metadata if isinstance(doc_candidate.metadata, dict) else {}
            row_file_id, row_file_name = _extract_file_metadata(metadata)
            parent_id = _resolve_semantic_parent_id(doc_candidate)
            score = _safe_float(item[1])

            hit = {
                "source": "semantic",
                "query": anchor,
                "child_chunk_id": child_chunk_id,
                "parent_id": parent_id,
                "file_id": row_file_id,
                "file_name": row_file_name,
                "score": score,
            }
            anchor_hits.append(hit)

            entry = child_aggregate.setdefault(
                child_chunk_id,
                {
                    "child_chunk_id": child_chunk_id,
                    "parent_id": parent_id,
                    "file_id": row_file_id,
                    "file_name": row_file_name,
                    "queries": set(),
                    "scores": [],
                },
            )
            entry["queries"].add(anchor)
            if isinstance(score, float):
                entry["scores"].append(score)
            if not entry.get("parent_id") and parent_id:
                entry["parent_id"] = parent_id

        semantic_query_hits[anchor] = anchor_hits

    new_child_chunks: list[dict[str, Any]] = []
    for entry in child_aggregate.values():
        new_child_chunks.append(
            {
                "child_chunk_id": entry["child_chunk_id"],
                "parent_id": entry.get("parent_id"),
                "file_id": entry["file_id"],
                "file_name": entry["file_name"],
                "query_hit_count": len(entry["queries"]),
                "avg_semantic_score": _average_or_none(entry["scores"]),
            }
        )
    new_child_chunks.sort(
        key=lambda item: (
            -int(item.get("query_hit_count", 0)),
            -(item.get("avg_semantic_score") if isinstance(item.get("avg_semantic_score"), float) else -10**9),
            str(item.get("child_chunk_id", "")),
        )
    )

    new_parent_ids: list[str] = []
    for child_item in new_child_chunks:
        parent_id = str(child_item.get("parent_id") or "").strip()
        if not parent_id or parent_id in seen_parent_ids:
            continue
        if parent_id in new_parent_ids:
            continue
        new_parent_ids.append(parent_id)

    new_parent_chunks: list[dict[str, Any]] = []
    if new_parent_ids:
        try:
            parent_rows = await vector_search._fetch_parent_chunks(
                new_parent_ids,
                cache=retrieval_cache,
            )
        except TypeError:
            parent_rows = await vector_search._fetch_parent_chunks(new_parent_ids)

        for parent_id, raw_doc in zip(new_parent_ids, parent_rows):
            if not isinstance(raw_doc, dict):
                continue
            metadata = raw_doc.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            row_file_id, row_file_name = _extract_file_metadata(metadata)
            new_parent_chunks.append(
                {
                    "parent_id": parent_id,
                    "chunk_number": _extract_parent_chunk_number(metadata),
                    "page_content": str(raw_doc.get("page_content") or ""),
                    "file_id": row_file_id if row_file_id != "unknown" else file_id,
                    "file_name": row_file_name if row_file_name != "unknown" else file_name,
                }
            )
        new_parent_chunks.sort(key=_parent_sort_key)

    return {
        "semantic_query_modes": semantic_query_modes,
        "query_hits": {
            "semantic": semantic_query_hits,
        },
        "new_child_chunks": new_child_chunks,
        "new_parent_chunks": new_parent_chunks,
    }


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
            expanded_child_chunks = base.get("expanded_child_chunks", []) if isinstance(base, dict) else []
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
                    "expanded_child_chunks": (
                        expanded_child_chunks if isinstance(expanded_child_chunks, list) else []
                    ),
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
                "expanded_child_chunks": (
                    node3_item.get("expanded_child_chunks", [])
                    if isinstance(node3_item.get("expanded_child_chunks"), list)
                    else []
                ),
                "semantic_anchors": semantic_anchors,
            }
        )
    return candidates


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
            "promotion_reason": "strong_signal_file",
        }
        for file_item in filter_candidates
        if bool(file_item.get("strong_signal_file", False))
    ]
    strong_signal_file_keys = {
        _file_key(
            str(file_item.get("file_id") or "unknown"),
            str(file_item.get("file_name") or "unknown"),
        )
        for file_item in strong_signal_files
    }

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
    promoted_candidates: list[dict[str, Any]] = []
    dropped_files: list[dict[str, Any]] = []
    run_id = state.get("run_id")

    for file_item in filter_candidates:
        file_id = str(file_item.get("file_id") or "unknown")
        file_name = str(file_item.get("file_name") or "unknown")

        parent_chunks = [
            dict(parent_chunk)
            for parent_chunk in file_item.get("parent_chunks", [])
            if isinstance(parent_chunk, dict)
        ]
        semantic_anchors = _normalize_anchors(file_item.get("semantic_anchors"))
        if not semantic_anchors:
            semantic_anchors = fallback_semantic_anchors

        seen_child_chunk_ids: set[str] = set()
        for child_item in file_item.get("expanded_child_chunks", []):
            if not isinstance(child_item, dict):
                continue
            child_id = str(child_item.get("child_chunk_id") or "").strip()
            if child_id:
                seen_child_chunk_ids.add(child_id)

        seen_parent_ids: set[str] = set()
        for parent_chunk in parent_chunks:
            parent_id = str(parent_chunk.get("parent_id") or "").strip()
            if parent_id:
                seen_parent_ids.add(parent_id)

        final_normalized = _normalize_file_filter_result(
            {},
            available_chunk_numbers=_extract_available_chunk_numbers(parent_chunks),
            fallback_reason="No LLM evaluation completed.",
        )
        exhaustion_reason = "not_started"
        round_history: list[dict[str, Any]] = []
        round_index = 1

        while True:
            available_chunk_numbers = _extract_available_chunk_numbers(parent_chunks)
            parent_chunks_payload = _build_parent_chunks_prompt_payload(parent_chunks)

            try:
                llm_text, usage = await llm_client._call_llm(
                    system_prompt=FILE_FILTERING_SYSTEM_PROMPT,
                    user_message=FILE_FILTERING_USER_PROMPT.format(
                        goal=goal,
                        constraint=constraint,
                        parent_chunks=parent_chunks_payload,
                    ),
                    session=state.get("_session"),
                    run_id=run_id,
                    step="file_filtering",
                    max_tokens=512,
                )
                llm_calls_made += 1
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
                    fallback_reason=f"Fallback reject due to file filtering error: {error}",
                )

            round_record: dict[str, Any] = {
                "round_index": round_index,
                "decision": final_normalized["decision"],
                "confidence": float(final_normalized["confidence"]),
                "reasoning_summary": final_normalized["reasoning_summary"],
                "suggested_chunk_numbers": final_normalized["suggested_chunk_numbers"],
                "parent_chunk_count_before": len(parent_chunks),
                "new_child_chunk_count": 0,
                "new_parent_chunk_count": 0,
                "semantic_query_modes": {},
                "round_exhaustion_reason": "",
            }

            if float(final_normalized["confidence"]) < POTENTIAL_MATCH_PROMOTION_THRESHOLD:
                exhaustion_reason = "confidence_below_threshold"
                round_record["round_exhaustion_reason"] = exhaustion_reason
                round_history.append(round_record)
                break

            if not semantic_anchors:
                exhaustion_reason = "no_semantic_anchors"
                round_record["round_exhaustion_reason"] = exhaustion_reason
                round_history.append(round_record)
                break

            expansion = await _expand_file_context_one_round(
                file_id=file_id,
                file_name=file_name,
                semantic_anchors=semantic_anchors,
                seen_child_chunk_ids=seen_child_chunk_ids,
                seen_parent_ids=seen_parent_ids,
                retrieval_cache=retrieval_cache,
            )
            new_child_chunks = expansion.get("new_child_chunks", [])
            new_parent_chunks = expansion.get("new_parent_chunks", [])
            round_record["semantic_query_modes"] = expansion.get("semantic_query_modes", {})
            round_record["new_child_chunk_count"] = len(new_child_chunks) if isinstance(new_child_chunks, list) else 0
            round_record["new_parent_chunk_count"] = len(new_parent_chunks) if isinstance(new_parent_chunks, list) else 0

            if not new_parent_chunks:
                exhaustion_reason = "no_new_parent_chunks"
                round_record["round_exhaustion_reason"] = exhaustion_reason
                round_history.append(round_record)
                break

            for child_item in new_child_chunks:
                if not isinstance(child_item, dict):
                    continue
                child_id = str(child_item.get("child_chunk_id") or "").strip()
                if child_id:
                    seen_child_chunk_ids.add(child_id)

            added_parent_count = 0
            for parent_chunk in new_parent_chunks:
                if not isinstance(parent_chunk, dict):
                    continue
                parent_id = str(parent_chunk.get("parent_id") or "").strip()
                if not parent_id or parent_id in seen_parent_ids:
                    continue
                seen_parent_ids.add(parent_id)
                parent_chunks.append(parent_chunk)
                added_parent_count += 1

            if added_parent_count <= 0:
                exhaustion_reason = "no_new_parent_chunks"
                round_record["round_exhaustion_reason"] = exhaustion_reason
                round_history.append(round_record)
                break

            round_record["round_exhaustion_reason"] = "continue"
            round_history.append(round_record)
            round_index += 1

        is_promoted = final_normalized["decision"] == "direct_match" or (
            final_normalized["decision"] == "potential_match"
            and float(final_normalized["confidence"]) >= POTENTIAL_MATCH_PROMOTION_THRESHOLD
        )

        evaluation = {
            "file_id": file_id,
            "file_name": file_name,
            "parent_chunk_count": len(parent_chunks),
            "semantic_anchor_count": len(semantic_anchors),
            "round_count": len(round_history),
            "round_history": round_history,
            "exhaustion_reason": exhaustion_reason,
            **final_normalized,
            "promoted": is_promoted,
            "strong_signal_file": bool(file_item.get("strong_signal_file", False)),
        }
        file_evaluations.append(evaluation)

        if is_promoted:
            promoted_candidates.append(
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "promotion_reason": (
                        "direct_match"
                        if final_normalized["decision"] == "direct_match"
                        else "potential_match_confidence_threshold"
                    ),
                    "confidence": float(final_normalized["confidence"]),
                    "decision": final_normalized["decision"],
                }
            )
        else:
            dropped_files.append(
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "decision": final_normalized["decision"],
                    "confidence": float(final_normalized["confidence"]),
                    "drop_reason": (
                        "reject_decision"
                        if final_normalized["decision"] == "reject"
                        else "potential_match_below_threshold"
                    ),
                }
            )

    promoted_by_key: dict[str, dict[str, Any]] = {}
    for item in promoted_candidates:
        file_id = str(item.get("file_id") or "").strip()
        file_name = str(item.get("file_name") or "").strip()
        key = _file_key(file_id, file_name)
        if key not in promoted_by_key:
            promoted_by_key[key] = item

    node_result = {
        "batch_id": resolved_batch_id,
        "goal": goal,
        "constraint": constraint,
        "confidence_threshold_for_potential_match": POTENTIAL_MATCH_PROMOTION_THRESHOLD,
        "strong_signal_files": strong_signal_files,
        "evaluations": file_evaluations,
        "promoted_files": list(promoted_by_key.values()) if promoted_by_key else [],
        "dropped_files": dropped_files,
        "run_summary": {
            "strong_signal_file_count": len(strong_signal_files),
            "evaluated_file_count": len(file_evaluations),
            "evaluated_non_strong_file_count": sum(
                1
                for evaluation in file_evaluations
                if _file_key(
                    str(evaluation.get("file_id") or ""),
                    str(evaluation.get("file_name") or ""),
                )
                not in strong_signal_file_keys
            ),
            "promoted_file_count": len(promoted_by_key),
            "dropped_file_count": len(dropped_files),
            "llm_round_count": sum(
                int(evaluation.get("round_count", 0) or 0)
                for evaluation in file_evaluations
            ),
            "cache_stats": vector_search._snapshot_retrieval_cache_stats(retrieval_cache),
        },
    }
    return node_result, usage_totals, llm_calls_made


async def file_filtering_node(state: RetrievalBriefState) -> dict:
    """Node 4 wrapper: run one file-filtering batch."""
    print("[Agent v2 - Node 4] Running file filtering...")
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

