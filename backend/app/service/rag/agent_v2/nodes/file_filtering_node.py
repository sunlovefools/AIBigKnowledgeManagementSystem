"""Node 4: file filtering."""
from __future__ import annotations

from typing import Any

from ..prompts.retrieval_brief_prompts import (
    FILE_FILTERING_SYSTEM_PROMPT,
    FILE_FILTERING_USER_PROMPT,
)
from ..services import llm_client
from ..shared.constants import POTENTIAL_MATCH_PROMOTION_THRESHOLD
from ..shared.logging import log_modification_agent_search_group
from ..shared.normalization import (
    _normalize_constraint,
    _normalize_goal,
    _parse_json_object,
)
from ..shared.search_utils import (
    _build_parent_chunks_prompt_payload,
    _normalize_file_filter_result,
)
from ..state.retrieval_brief_state import RetrievalBriefState


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
    node2_files = search_group_result.get("files") if isinstance(search_group_result, dict) else []
    if not isinstance(node2_files, list):
        node2_files = []

    strong_signal_files = [
        {
            "file_id": str(file_item.get("file_id") or "unknown"),
            "file_name": str(file_item.get("file_name") or "unknown"),
            "promotion_reason": "strong_signal_file",
        }
        for file_item in node2_files
        if isinstance(file_item, dict) and bool(file_item.get("strong_signal_file", False))
    ]

    node3_files = expansion_result.get("files") if isinstance(expansion_result, dict) else []
    if not isinstance(node3_files, list):
        node3_files = []

    goal = _normalize_goal(state.get("goal"), state.get("user_instructions", ""))
    constraint = _normalize_constraint(state.get("constraint"))
    resolved_batch_id = int(batch_id or 1)

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

    for file_item in node3_files:
        if not isinstance(file_item, dict):
            continue

        file_id = str(file_item.get("file_id") or "unknown")
        file_name = str(file_item.get("file_name") or "unknown")
        parent_chunks = file_item.get("parent_chunks", [])
        if not isinstance(parent_chunks, list):
            parent_chunks = []

        available_chunk_numbers = {
            chunk_number
            for chunk_number in (
                parent_chunk.get("chunk_number")
                for parent_chunk in parent_chunks
                if isinstance(parent_chunk, dict)
            )
            if isinstance(chunk_number, int)
        }

        parent_chunks_payload = str(
            file_item.get("parent_chunks_prompt_payload")
            or _build_parent_chunks_prompt_payload(parent_chunks)
        )

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
            normalized = _normalize_file_filter_result(
                parsed,
                available_chunk_numbers=available_chunk_numbers,
                fallback_reason="Model response normalized.",
            )
        except Exception as error:
            normalized = _normalize_file_filter_result(
                {},
                available_chunk_numbers=available_chunk_numbers,
                fallback_reason=f"Fallback reject due to file filtering error: {error}",
            )

        is_promoted = normalized["decision"] == "direct_match" or (
            normalized["decision"] == "potential_match"
            and float(normalized["confidence"]) >= POTENTIAL_MATCH_PROMOTION_THRESHOLD
        )

        evaluation = {
            "file_id": file_id,
            "file_name": file_name,
            "parent_chunk_count": len(parent_chunks),
            **normalized,
            "promoted": is_promoted,
        }
        file_evaluations.append(evaluation)

        if is_promoted:
            promoted_candidates.append(
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "promotion_reason": (
                        "direct_match"
                        if normalized["decision"] == "direct_match"
                        else "potential_match_confidence_threshold"
                    ),
                    "confidence": float(normalized["confidence"]),
                    "decision": normalized["decision"],
                }
            )
        else:
            dropped_files.append(
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "decision": normalized["decision"],
                    "confidence": float(normalized["confidence"]),
                    "drop_reason": (
                        "reject_decision"
                        if normalized["decision"] == "reject"
                        else "potential_match_below_threshold"
                    ),
                }
            )

    promoted_by_key: dict[str, dict[str, Any]] = {}
    for item in strong_signal_files + promoted_candidates:
        file_id = str(item.get("file_id") or "").strip()
        file_name = str(item.get("file_name") or "").strip()
        key = f"{file_id}::{file_name}"
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
            "evaluated_non_strong_file_count": len(file_evaluations),
            "promoted_file_count": len(promoted_by_key),
            "dropped_file_count": len(dropped_files),
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
        }

