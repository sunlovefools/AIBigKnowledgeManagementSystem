"""Iterative orchestrator node for Agentic Modification."""
from __future__ import annotations

import asyncio
from contextlib import suppress

from ..shared.constants import (
    ITERATION_CONTINUE_PROMOTED_RATIO_THRESHOLD,
    POTENTIAL_MATCH_PROMOTION_THRESHOLD,
    SEARCH_TOP_K,
)
from ..shared.logging import log_modification_agent_search_group
from ..shared.search_utils import _extract_fetched_file_ids_from_search_batch
from ..state.retrieval_brief_state import RetrievalBriefState
from .clue_chunk_explorer_node import run_clue_chunk_explorer_batch
from .file_filtering_node import run_file_filtering_batch
from .non_strong_signal_file_context_expansion_node import (
    run_non_strong_signal_file_context_expansion_batch,
)
from .search_and_group_node import run_search_and_group_batch


async def iterative_search_filter_orchestrator_node(state: RetrievalBriefState) -> dict:
    """
    Entry point of Node 2: Iterative Search/Filter Orchestrator.

    Iterative orchestrator:
    - Repeats search/group batches with file-id exclusion.
    - Prefetches one next search batch while current expansion/filtering runs.
    - Continues only when promoted/evaluated ratio of current filtering batch meets threshold.
    """
    print("[Agentic Modification - Orchestrator] Running iterative search/filter loop...")
    run_id = state.get("run_id")

    excluded_file_ids: set[str] = set()
    current_batch_id = 1

    node2_batches: list[dict] = []
    node3_batches: list[dict] = []
    node4_batches: list[dict] = []
    node5_batches: list[dict] = []

    promoted_global_by_key: dict[str, dict] = {}
    fetched_file_ids_global: set[str] = set()
    confirmed_parent_chunk_refs_global_by_key: dict[str, dict] = {}

    usage_prompt_increment = 0
    usage_completion_increment = 0
    usage_total_increment = 0
    llm_calls_increment = 0

    termination_reason = "search_exhausted"

    try:
        # Start the first search batch without exclusion to get initial results and signals
        current_search_batch = await run_search_and_group_batch(
            state,
            excluded_file_ids=excluded_file_ids,
            batch_id=current_batch_id,
        )

        while True:
            # Append the current search batch results to the list of batches for node 2
            node2_batches.append(current_search_batch)
            log_modification_agent_search_group(
                run_id=run_id,
                step="search_and_group_batch",
                payload=current_search_batch,
            )

            current_files = current_search_batch.get("files", [])
            if not isinstance(current_files, list):
                current_files = []

            if not current_files:
                termination_reason = "search_exhausted"
                break

            current_fetched_ids = _extract_fetched_file_ids_from_search_batch(current_search_batch)
            fetched_file_ids_global.update(current_fetched_ids)
            next_excluded_file_ids = excluded_file_ids | current_fetched_ids

            prefetch_task: asyncio.Task = asyncio.create_task(
                run_search_and_group_batch(
                    state,
                    excluded_file_ids=next_excluded_file_ids,
                    batch_id=current_batch_id + 1,
                )
            )
            # Yield once so prefetch starts before current batch filtering work.
            await asyncio.sleep(0)

            current_expansion_batch = await run_non_strong_signal_file_context_expansion_batch(
                state,
                search_group_result=current_search_batch,
                batch_id=current_batch_id,
            )
            node3_batches.append(current_expansion_batch)
            log_modification_agent_search_group(
                run_id=run_id,
                step="non_strong_signal_file_context_expansion_batch",
                payload=current_expansion_batch,
            )

            current_filtering_batch, usage_totals, llm_calls_made = await run_file_filtering_batch(
                state,
                search_group_result=current_search_batch,
                expansion_result=current_expansion_batch,
                batch_id=current_batch_id,
            )
            node4_batches.append(current_filtering_batch)
            log_modification_agent_search_group(
                run_id=run_id,
                step="file_filtering_batch",
                payload=current_filtering_batch,
            )

            usage_prompt_increment += int(usage_totals.get("prompt_tokens", 0) or 0)
            usage_completion_increment += int(usage_totals.get("completion_tokens", 0) or 0)
            usage_total_increment += int(usage_totals.get("total_tokens", 0) or 0)
            llm_calls_increment += int(llm_calls_made or 0)

            current_clue_batch, clue_usage_totals, clue_llm_calls_made = await run_clue_chunk_explorer_batch(
                state,
                file_filtering_result=current_filtering_batch,
                batch_id=current_batch_id,
            )
            node5_batches.append(current_clue_batch)
            log_modification_agent_search_group(
                run_id=run_id,
                step="clue_chunk_explorer_batch",
                payload=current_clue_batch,
            )

            usage_prompt_increment += int(clue_usage_totals.get("prompt_tokens", 0) or 0)
            usage_completion_increment += int(clue_usage_totals.get("completion_tokens", 0) or 0)
            usage_total_increment += int(clue_usage_totals.get("total_tokens", 0) or 0)
            llm_calls_increment += int(clue_llm_calls_made or 0)

            batch_confirmed_refs = current_clue_batch.get("merged_confirmed_parent_chunk_refs", [])
            if not isinstance(batch_confirmed_refs, list):
                batch_confirmed_refs = []
            for ref in batch_confirmed_refs:
                if not isinstance(ref, dict):
                    continue
                file_id = str(ref.get("file_id") or "").strip()
                parent_chunk_number = ref.get("parent_chunk_number")
                if not file_id or not isinstance(parent_chunk_number, int):
                    continue
                key = f"{file_id}::{parent_chunk_number}"
                confirmed_parent_chunk_refs_global_by_key.setdefault(key, ref)

            batch_promoted_files = current_filtering_batch.get("promoted_files", [])
            if not isinstance(batch_promoted_files, list):
                batch_promoted_files = []
            for item in batch_promoted_files:
                if not isinstance(item, dict):
                    continue
                file_id = str(item.get("file_id") or "").strip()
                file_name = str(item.get("file_name") or "").strip()
                if not file_id:
                    continue
                promoted_global_by_key.setdefault(f"{file_id}::{file_name}", item)

            filtering_summary = current_filtering_batch.get("run_summary", {})
            evaluated_count = 0
            promoted_count = 0
            if isinstance(filtering_summary, dict):
                evaluated_count = int(filtering_summary.get("evaluated_file_count", 0) or 0)
                promoted_count = int(filtering_summary.get("promoted_file_count", 0) or 0)
            promoted_ratio = (
                float(promoted_count) / float(evaluated_count)
                if evaluated_count > 0
                else 0.0
            )

            if promoted_ratio < ITERATION_CONTINUE_PROMOTED_RATIO_THRESHOLD:
                termination_reason = "promotion_ratio_below_threshold"
                if not prefetch_task.done():
                    prefetch_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await prefetch_task
                break

            current_search_batch = await prefetch_task
            excluded_file_ids = next_excluded_file_ids
            current_batch_id += 1

        latest_node2_batch = node2_batches[-1] if node2_batches else {}
        latest_node3_batch = node3_batches[-1] if node3_batches else {}
        latest_node4_batch = node4_batches[-1] if node4_batches else {}
        latest_node5_batch = node5_batches[-1] if node5_batches else {}

        node2_result = {
            "batches": node2_batches,
            "latest_batch": latest_node2_batch,
            "queries": latest_node2_batch.get("queries", {}) if isinstance(latest_node2_batch, dict) else {},
            "query_hits": latest_node2_batch.get("query_hits", {}) if isinstance(latest_node2_batch, dict) else {},
            "children": latest_node2_batch.get("children", []) if isinstance(latest_node2_batch, dict) else [],
            "files": latest_node2_batch.get("files", []) if isinstance(latest_node2_batch, dict) else [],
            "run_summary": {
                "batch_count": len(node2_batches),
                "termination_reason": termination_reason,
                "top_k_per_query": (
                    latest_node2_batch.get("run_summary", {}).get("top_k_per_query", SEARCH_TOP_K)
                    if isinstance(latest_node2_batch, dict)
                    else SEARCH_TOP_K
                ),
                "total_hits": sum(
                    int(batch.get("run_summary", {}).get("total_hits", 0) or 0)
                    for batch in node2_batches
                    if isinstance(batch, dict)
                ),
                "total_files": sum(
                    int(batch.get("run_summary", {}).get("total_files", 0) or 0)
                    for batch in node2_batches
                    if isinstance(batch, dict)
                ),
                "strong_signal_file_count": sum(
                    int(batch.get("run_summary", {}).get("strong_signal_file_count", 0) or 0)
                    for batch in node2_batches
                    if isinstance(batch, dict)
                ),
                "unique_fetched_file_count": len(fetched_file_ids_global),
                "unique_fetched_file_ids": sorted(fetched_file_ids_global) if fetched_file_ids_global else "none",
            },
        }

        node3_result = {
            "batches": node3_batches,
            "latest_batch": latest_node3_batch,
            "queries": latest_node3_batch.get("queries", {}) if isinstance(latest_node3_batch, dict) else {},
            "files": latest_node3_batch.get("files", []) if isinstance(latest_node3_batch, dict) else [],
            "run_summary": {
                "batch_count": len(node3_batches),
                "termination_reason": termination_reason,
                "expanded_file_count": sum(
                    int(batch.get("run_summary", {}).get("expanded_file_count", 0) or 0)
                    for batch in node3_batches
                    if isinstance(batch, dict)
                ),
                "expanded_parent_chunk_count": sum(
                    int(batch.get("run_summary", {}).get("expanded_parent_chunk_count", 0) or 0)
                    for batch in node3_batches
                    if isinstance(batch, dict)
                ),
            },
        }

        node4_result = {
            "batches": node4_batches,
            "latest_batch": latest_node4_batch,
            "goal": latest_node4_batch.get("goal", "") if isinstance(latest_node4_batch, dict) else "",
            "constraint": latest_node4_batch.get("constraint", "None") if isinstance(latest_node4_batch, dict) else "None",
            "confidence_threshold_for_potential_match": POTENTIAL_MATCH_PROMOTION_THRESHOLD,
            "strong_signal_files": (
                latest_node4_batch.get("strong_signal_files", [])
                if isinstance(latest_node4_batch, dict)
                else []
            ),
            "evaluations": latest_node4_batch.get("evaluations", []) if isinstance(latest_node4_batch, dict) else [],
            "dropped_files": latest_node4_batch.get("dropped_files", []) if isinstance(latest_node4_batch, dict) else [],
            "promoted_files": list(promoted_global_by_key.values()),
            "run_summary": {
                "batch_count": len(node4_batches),
                "termination_reason": termination_reason,
                "promoted_file_count": len(promoted_global_by_key),
                "evaluated_non_strong_file_count": sum(
                    int(batch.get("run_summary", {}).get("evaluated_non_strong_file_count", 0) or 0)
                    for batch in node4_batches
                    if isinstance(batch, dict)
                ),
                "dropped_file_count": sum(
                    int(batch.get("run_summary", {}).get("dropped_file_count", 0) or 0)
                    for batch in node4_batches
                    if isinstance(batch, dict)
                ),
                "promoted_ratio_current_batch": (
                    float(
                        latest_node4_batch.get("run_summary", {}).get("promoted_ratio_current_batch", 0.0)
                    )
                    if isinstance(latest_node4_batch, dict)
                    else 0.0
                ),
                "continue_ratio_threshold": ITERATION_CONTINUE_PROMOTED_RATIO_THRESHOLD,
                "confidence_threshold_for_potential_match": POTENTIAL_MATCH_PROMOTION_THRESHOLD,
            },
        }

        node5_result = {
            "batches": node5_batches,
            "latest_batch": latest_node5_batch,
            "goal": latest_node5_batch.get("goal", "") if isinstance(latest_node5_batch, dict) else "",
            "constraint": latest_node5_batch.get("constraint", "None") if isinstance(latest_node5_batch, dict) else "None",
            "explorations": latest_node5_batch.get("explorations", []) if isinstance(latest_node5_batch, dict) else [],
            "confirmed_parent_chunks_by_file": (
                latest_node5_batch.get("confirmed_parent_chunks_by_file", [])
                if isinstance(latest_node5_batch, dict)
                else []
            ),
            "merged_confirmed_parent_chunk_refs": list(confirmed_parent_chunk_refs_global_by_key.values()),
            "run_summary": {
                "batch_count": len(node5_batches),
                "termination_reason": termination_reason,
                "exploration_count": sum(
                    int(batch.get("run_summary", {}).get("exploration_count", 0) or 0)
                    for batch in node5_batches
                    if isinstance(batch, dict)
                ),
                "confirmed_exploration_count": sum(
                    int(batch.get("run_summary", {}).get("confirmed_exploration_count", 0) or 0)
                    for batch in node5_batches
                    if isinstance(batch, dict)
                ),
                "dead_end_count": sum(
                    int(batch.get("run_summary", {}).get("dead_end_count", 0) or 0)
                    for batch in node5_batches
                    if isinstance(batch, dict)
                ),
                "confirmed_file_count": len(
                    {
                        str(ref.get("file_id") or "").strip()
                        for ref in confirmed_parent_chunk_refs_global_by_key.values()
                        if isinstance(ref, dict) and str(ref.get("file_id") or "").strip()
                    }
                ),
                "confirmed_parent_chunk_ref_count": len(confirmed_parent_chunk_refs_global_by_key),
                "tool_call_count": sum(
                    int(batch.get("run_summary", {}).get("tool_call_count", 0) or 0)
                    for batch in node5_batches
                    if isinstance(batch, dict)
                ),
                "llm_round_count": sum(
                    int(batch.get("run_summary", {}).get("llm_round_count", 0) or 0)
                    for batch in node5_batches
                    if isinstance(batch, dict)
                ),
            },
        }

        log_modification_agent_search_group(
            run_id=run_id,
            step="iterative_search_filter_orchestrator",
            payload={
                "node2_run_summary": node2_result["run_summary"],
                "node3_run_summary": node3_result["run_summary"],
                "node4_run_summary": node4_result["run_summary"],
                "node5_run_summary": node5_result["run_summary"],
            },
        )

        return {
            "node2_search_group_result": node2_result,
            "node3_non_strong_signal_file_context_expansion_result": node3_result,
            "node4_file_filtering_result": node4_result,
            "node5_clue_chunk_explorer_result": node5_result,
            "token_prompt_total": int(state.get("token_prompt_total", 0) or 0) + usage_prompt_increment,
            "token_completion_total": int(state.get("token_completion_total", 0) or 0) + usage_completion_increment,
            "token_total": int(state.get("token_total", 0) or 0) + usage_total_increment,
            "llm_call_count": int(state.get("llm_call_count", 0) or 0) + llm_calls_increment,
        }
    except Exception as error:
        error_message = f"iterative search/filter orchestrator failed: {error}"
        print(error_message)
        log_modification_agent_search_group(
            run_id=run_id,
            step="iterative_search_filter_orchestrator",
            payload={
                "error": error_message,
            },
        )
        return {
            "error": error_message,
        }
