"""Node 2: search and grouping."""
from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.documents import Document

from ..services import vector_search
from ..shared.constants import SEARCH_EXCLUSION_OVERFETCH_MULTIPLIERS, SEARCH_TOP_K
from ..shared.logging import log_modification_agent_search_group
from ..shared.normalization import _normalize_anchors, _normalize_excluded_file_ids
from ..shared.search_utils import (
    _average_or_none,
    _extract_fetched_file_ids_from_search_batch,
    _extract_file_metadata,
    _resolve_lexical_child_chunk_id,
    _resolve_semantic_child_chunk_id,
    _safe_float,
)
from ..state.retrieval_brief_state import RetrievalBriefState


async def run_search_and_group_batch(
    state: RetrievalBriefState,
    *,
    excluded_file_ids: set[str] | None = None,
    batch_id: int | None = None,
) -> dict[str, Any]:
    """Reusable search/group batch runner with optional file-id exclusion."""
    lexical_anchors = _normalize_anchors(state.get("lexical_anchors"))
    semantic_anchors = _normalize_anchors(state.get("semantic_anchors"))
    if not lexical_anchors and not semantic_anchors:
        legacy_anchors = _normalize_anchors(state.get("anchors"))
        lexical_anchors = legacy_anchors
        semantic_anchors = legacy_anchors

    excluded_ids = _normalize_excluded_file_ids(excluded_file_ids)
    resolved_batch_id = int(batch_id or 1)

    async def _run_lexical_query(anchor: str) -> tuple[str, list[dict[str, Any]], str]:
        hits: list[dict[str, Any]] = []
        seen_child_ids: set[str] = set()
        used_overfetch = False

        for index, multiplier in enumerate(SEARCH_EXCLUSION_OVERFETCH_MULTIPLIERS):
            request_k = max(SEARCH_TOP_K, SEARCH_TOP_K * int(multiplier))
            try:
                rows = await vector_search._run_lexical_search(
                    query=anchor,
                    top_k=request_k,
                    excluded_file_ids=excluded_ids,
                )
            except TypeError:
                # Backward-compatible fallback for test monkeypatches using old signature.
                rows = await vector_search._run_lexical_search(query=anchor, top_k=request_k)
            if index > 0:
                used_overfetch = True

            for row in rows:
                metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                child_chunk_id = _resolve_lexical_child_chunk_id(row, query=anchor)
                if child_chunk_id in seen_child_ids:
                    continue

                file_id, file_name = _extract_file_metadata(metadata)
                if file_id in excluded_ids:
                    continue

                seen_child_ids.add(child_chunk_id)
                hits.append(
                    {
                        "source": "lexical",
                        "query": anchor,
                        "child_chunk_id": child_chunk_id,
                        "file_id": file_id,
                        "file_name": file_name,
                    }
                )
                if len(hits) >= SEARCH_TOP_K:
                    break
            if len(hits) >= SEARCH_TOP_K:
                break

        query_mode = "overfetch_post_filter" if used_overfetch else "base"
        return anchor, hits[:SEARCH_TOP_K], query_mode

    async def _run_semantic_query(anchor: str) -> tuple[str, list[dict[str, Any]], str]:
        hits: list[dict[str, Any]] = []
        seen_child_ids: set[str] = set()
        used_overfetch = False

        for index, multiplier in enumerate(SEARCH_EXCLUSION_OVERFETCH_MULTIPLIERS):
            request_k = max(SEARCH_TOP_K, SEARCH_TOP_K * int(multiplier))
            try:
                items = await vector_search._run_semantic_search(
                    query=anchor,
                    top_k=request_k,
                    excluded_file_ids=excluded_ids,
                )
            except TypeError:
                # Backward-compatible fallback for test monkeypatches using old signature.
                items = await vector_search._run_semantic_search(query=anchor, top_k=request_k)
            if index > 0:
                used_overfetch = True

            for item in items:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                doc_candidate = item[0]
                if not isinstance(doc_candidate, Document):
                    continue

                child_chunk_id = _resolve_semantic_child_chunk_id(doc_candidate, query=anchor)
                if child_chunk_id in seen_child_ids:
                    continue

                metadata = doc_candidate.metadata if isinstance(doc_candidate.metadata, dict) else {}
                file_id, file_name = _extract_file_metadata(metadata)
                if file_id in excluded_ids:
                    continue

                seen_child_ids.add(child_chunk_id)
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
                if len(hits) >= SEARCH_TOP_K:
                    break
            if len(hits) >= SEARCH_TOP_K:
                break

        query_mode = "overfetch_post_filter" if used_overfetch else "base"
        return anchor, hits[:SEARCH_TOP_K], query_mode

    lexical_results = await asyncio.gather(*[_run_lexical_query(anchor) for anchor in lexical_anchors])
    semantic_results = await asyncio.gather(*[_run_semantic_query(anchor) for anchor in semantic_anchors])

    lexical_hits_by_query = {anchor: hits for anchor, hits, _ in lexical_results}
    semantic_hits_by_query = {anchor: hits for anchor, hits, _ in semantic_results}
    lexical_query_modes = {anchor: mode for anchor, _, mode in lexical_results}
    semantic_query_modes = {anchor: mode for anchor, _, mode in semantic_results}

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
        strong_signal_chunk = child_entry["lexical_hit_count"] > 0 and child_entry["semantic_hit_count"] > 0
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
        f"{item['file_id']}::{item['file_name']}" for item in child_results if item["strong_signal_chunk"]
    }
    for file_entry in file_agg.values():
        avg_semantic_score = _average_or_none(file_entry["semantic_scores"])
        has_both_sources = file_entry["lexical_hit_count"] > 0 and file_entry["semantic_hit_count"] > 0
        file_key = f"{file_entry['file_id']}::{file_entry['file_name']}"
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
    fetched_file_ids = sorted(_extract_fetched_file_ids_from_search_batch({"files": file_results}))

    return {
        "batch_id": resolved_batch_id,
        "excluded_file_ids": sorted(excluded_ids),
        "query_modes": {
            "lexical": lexical_query_modes,
            "semantic": semantic_query_modes,
        },
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
            "excluded_file_id_count": len(excluded_ids),
            "excluded_file_ids": sorted(excluded_ids) if excluded_ids else "none",
            "fetched_file_ids": fetched_file_ids if fetched_file_ids else "none",
            "strong_signal_chunk_count": sum(1 for item in child_results if item["strong_signal_chunk"]),
            "strong_signal_file_count": sum(1 for item in file_results if item["strong_signal_file"]),
            "strong_signal_chunks": strong_signal_chunk_refs if strong_signal_chunk_refs else "none",
            "strong_signal_files": strong_signal_file_refs if strong_signal_file_refs else "none",
        },
    }


async def search_and_group_node(state: RetrievalBriefState) -> dict:
    """Node 2 wrapper: run one search/group batch with no exclusions."""
    print("[Agent v2 - Node 2] Running search and grouping...")
    run_id = state.get("run_id")
    lexical_anchors = _normalize_anchors(state.get("lexical_anchors"))
    semantic_anchors = _normalize_anchors(state.get("semantic_anchors"))

    try:
        node_result = await run_search_and_group_batch(
            state,
            excluded_file_ids=set(),
            batch_id=1,
        )
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
