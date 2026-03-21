"""Iterative orchestrator node for Agentic Modification."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from ..shared.constants import SEARCH_TOP_K
from ..shared.logging import log_modification_agent_search_group
from ..shared.search_utils import _extract_fetched_file_ids_from_search_batch
from ..state.retrieval_brief_state import RetrievalBriefState
from .clue_chunk_explorer_node import run_clue_chunk_explorer_batch
from .file_filtering_node import run_file_filtering_batch
from .non_strong_signal_file_context_expansion_node import (
    run_non_strong_signal_file_context_expansion_batch,
)
from .search_and_group_node import run_search_and_group_batch


def _normalize_file_ids(raw_file_ids: Any) -> set[str]:
    if isinstance(raw_file_ids, set):
        candidates = list(raw_file_ids)
    elif isinstance(raw_file_ids, list):
        candidates = raw_file_ids
    elif isinstance(raw_file_ids, tuple):
        candidates = list(raw_file_ids)
    else:
        candidates = []

    normalized: set[str] = set()
    for item in candidates:
        file_id = str(item or "").strip()
        if file_id:
            normalized.add(file_id)
    return normalized


def _normalize_parent_chunk_refs(raw_refs: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_refs, list):
        return []

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_ref in raw_refs:
        if not isinstance(raw_ref, dict):
            continue
        file_id = str(raw_ref.get("file_id") or "").strip()
        file_name = str(raw_ref.get("file_name") or "").strip() or "unknown"
        parent_chunk_number = raw_ref.get("parent_chunk_number")
        if not file_id or not isinstance(parent_chunk_number, int):
            continue
        key = f"{file_id}::{parent_chunk_number}"
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "file_id": file_id,
                "file_name": file_name,
                "parent_chunk_number": parent_chunk_number,
            }
        )
    return normalized


def _normalize_excluded_child_chunk_ids_by_file(raw_entries: Any) -> dict[str, set[str]]:
    if not isinstance(raw_entries, list):
        return {}

    normalized: dict[str, set[str]] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        file_id = str(entry.get("file_id") or "").strip()
        if not file_id:
            continue
        raw_child_ids = entry.get("child_chunk_ids")
        if not isinstance(raw_child_ids, list):
            continue
        child_ids: set[str] = set()
        for item in raw_child_ids:
            child_id = str(item or "").strip()
            if child_id:
                child_ids.add(child_id)
        if child_ids:
            normalized.setdefault(file_id, set()).update(child_ids)
    return normalized


def _merge_excluded_child_chunk_maps(
    left_map: dict[str, set[str]],
    right_map: dict[str, set[str]],
) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {file_id: set(child_ids) for file_id, child_ids in left_map.items()}
    for file_id, child_ids in right_map.items():
        if not child_ids:
            continue
        merged.setdefault(file_id, set()).update(child_ids)
    return merged


def _serialize_excluded_child_chunk_map(raw_map: dict[str, set[str]]) -> list[dict[str, Any]]:
    return [
        {
            "file_id": file_id,
            "child_chunk_ids": sorted(child_ids),
        }
        for file_id, child_ids in sorted(raw_map.items())
        if child_ids
    ]


async def iterative_search_filter_orchestrator_node(state: RetrievalBriefState) -> dict:
    """
    Iterative orchestrator:
    - Repeats search/group batches using potential-file scoping.
    - Re-runs the same file scope while excluding previously seen child chunks.
    - Continues only when current Node-4 batch outputs potential parent chunks.
    """
    print("[Agentic Modification - Orchestrator] Running iterative search/filter loop...")
    run_id = state.get("run_id")

    current_batch_id = 1
    current_allowed_file_ids: set[str] | None = _normalize_file_ids(state.get("file_ids")) or None
    excluded_child_chunk_ids_by_file: dict[str, set[str]] = {}

    node2_batches: list[dict[str, Any]] = []
    node3_batches: list[dict[str, Any]] = []
    node4_batches: list[dict[str, Any]] = []
    node5_batches: list[dict[str, Any]] = []

    fetched_file_ids_global: set[str] = set()
    confirmed_parent_chunk_refs_global_by_key: dict[str, dict[str, Any]] = {}

    usage_prompt_increment = 0
    usage_completion_increment = 0
    usage_total_increment = 0
    llm_calls_increment = 0

    termination_reason = "search_exhausted"

    async def _run_search_batch(
        *,
        batch_id: int,
        allowed_file_ids_override: set[str] | None,
        excluded_child_chunk_ids_by_file_override: dict[str, set[str]],
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "excluded_file_ids": set(),
            "batch_id": batch_id,
        }
        if allowed_file_ids_override is not None:
            kwargs["allowed_file_ids_override"] = allowed_file_ids_override
        if excluded_child_chunk_ids_by_file_override:
            kwargs["excluded_child_chunk_ids_by_file"] = excluded_child_chunk_ids_by_file_override

        try:
            return await run_search_and_group_batch(state, **kwargs)
        except TypeError:
            # Backward compatibility for tests or call-sites monkeypatching the older signature.
            kwargs.pop("allowed_file_ids_override", None)
            kwargs.pop("excluded_child_chunk_ids_by_file", None)
            return await run_search_and_group_batch(state, **kwargs)

    try:
        current_search_batch = await _run_search_batch(
            batch_id=current_batch_id,
            allowed_file_ids_override=current_allowed_file_ids,
            excluded_child_chunk_ids_by_file_override=excluded_child_chunk_ids_by_file,
        )

        while True:
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

            fetched_file_ids_global.update(_extract_fetched_file_ids_from_search_batch(current_search_batch))

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

            potential_file_ids = _normalize_file_ids(current_filtering_batch.get("potential_file_ids", []))
            current_batch_exclusions = _normalize_excluded_child_chunk_ids_by_file(
                current_filtering_batch.get("excluded_child_chunk_ids_by_file", [])
            )
            next_excluded_child_chunk_ids_by_file = _merge_excluded_child_chunk_maps(
                excluded_child_chunk_ids_by_file,
                current_batch_exclusions,
            )

            prefetch_task: asyncio.Task | None = None
            if potential_file_ids:
                prefetch_task = asyncio.create_task(
                    _run_search_batch(
                        batch_id=current_batch_id + 1,
                        allowed_file_ids_override=potential_file_ids,
                        excluded_child_chunk_ids_by_file_override=next_excluded_child_chunk_ids_by_file,
                    )
                )
                await asyncio.sleep(0)

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

            batch_confirmed_refs = _normalize_parent_chunk_refs(
                current_filtering_batch.get("merged_confirmed_parent_chunk_refs", [])
            ) + _normalize_parent_chunk_refs(
                current_clue_batch.get("merged_confirmed_parent_chunk_refs", [])
            )
            for ref in batch_confirmed_refs:
                key = f"{ref['file_id']}::{ref['parent_chunk_number']}"
                confirmed_parent_chunk_refs_global_by_key.setdefault(key, ref)

            if not potential_file_ids:
                termination_reason = "no_potential_parent_chunks"
                if prefetch_task and not prefetch_task.done():
                    prefetch_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await prefetch_task
                break

            if prefetch_task is None:
                termination_reason = "search_exhausted"
                break

            current_search_batch = await prefetch_task
            current_allowed_file_ids = potential_file_ids
            excluded_child_chunk_ids_by_file = next_excluded_child_chunk_ids_by_file
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
                "excluded_child_chunk_ids_by_file": _serialize_excluded_child_chunk_map(
                    excluded_child_chunk_ids_by_file
                )
                or "none",
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
            "strong_signal_files": (
                latest_node4_batch.get("strong_signal_files", [])
                if isinstance(latest_node4_batch, dict)
                else []
            ),
            "evaluations": latest_node4_batch.get("evaluations", []) if isinstance(latest_node4_batch, dict) else [],
            "merged_confirmed_parent_chunk_refs": _normalize_parent_chunk_refs(
                latest_node4_batch.get("merged_confirmed_parent_chunk_refs", [])
                if isinstance(latest_node4_batch, dict)
                else []
            ),
            "merged_potential_parent_chunk_refs": _normalize_parent_chunk_refs(
                latest_node4_batch.get("merged_potential_parent_chunk_refs", [])
                if isinstance(latest_node4_batch, dict)
                else []
            ),
            "potential_file_ids": (
                sorted(_normalize_file_ids(latest_node4_batch.get("potential_file_ids", [])))
                if isinstance(latest_node4_batch, dict)
                else []
            ),
            "excluded_child_chunk_ids_by_file": (
                latest_node4_batch.get("excluded_child_chunk_ids_by_file", [])
                if isinstance(latest_node4_batch, dict)
                else []
            ),
            "run_summary": {
                "batch_count": len(node4_batches),
                "termination_reason": termination_reason,
                "evaluated_file_count": sum(
                    int(batch.get("run_summary", {}).get("evaluated_file_count", 0) or 0)
                    for batch in node4_batches
                    if isinstance(batch, dict)
                ),
                "confirmed_file_count": sum(
                    int(batch.get("run_summary", {}).get("confirmed_file_count", 0) or 0)
                    for batch in node4_batches
                    if isinstance(batch, dict)
                ),
                "potential_file_count": sum(
                    int(batch.get("run_summary", {}).get("potential_file_count", 0) or 0)
                    for batch in node4_batches
                    if isinstance(batch, dict)
                ),
                "confirmed_parent_chunk_ref_count": len(
                    {
                        f"{ref['file_id']}::{ref['parent_chunk_number']}"
                        for batch in node4_batches
                        if isinstance(batch, dict)
                        for ref in _normalize_parent_chunk_refs(batch.get("merged_confirmed_parent_chunk_refs", []))
                    }
                ),
                "potential_parent_chunk_ref_count": len(
                    {
                        f"{ref['file_id']}::{ref['parent_chunk_number']}"
                        for batch in node4_batches
                        if isinstance(batch, dict)
                        for ref in _normalize_parent_chunk_refs(batch.get("merged_potential_parent_chunk_refs", []))
                    }
                ),
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
