"""Node 2: search and grouping.

This node fans out lexical + semantic retrieval using anchors from Node 1, then
aggregates child/file-level hit statistics used by Node 3 and Node 4.
"""
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


def _normalize_allowed_file_ids(raw_allowed_file_ids: Any) -> set[str]:
    """
    Normalize the allowed file IDs from various possible input formats into a set of unique, non-empty strings.
    """
    if isinstance(raw_allowed_file_ids, set):
        candidates = list(raw_allowed_file_ids)
    elif isinstance(raw_allowed_file_ids, list):
        candidates = raw_allowed_file_ids
    elif isinstance(raw_allowed_file_ids, tuple):
        candidates = list(raw_allowed_file_ids)
    else:
        candidates = []

    normalized: set[str] = set()
    for item in candidates:
        value = str(item or "").strip()
        if value:
            normalized.add(value)
    return normalized


def _normalize_excluded_child_chunk_ids_by_file(
    raw_value: Any,
) -> dict[str, set[str]]:
    """
    Normalize the excluded child chunk IDs by file from various possible input formats into a dict mapping file IDs to sets of child chunk IDs.
    """
    if not isinstance(raw_value, dict):
        return {}

    normalized: dict[str, set[str]] = {}
    for raw_file_id, raw_child_ids in raw_value.items():
        file_id = str(raw_file_id or "").strip()
        if not file_id:
            continue
        if isinstance(raw_child_ids, set):
            child_candidates = list(raw_child_ids)
        elif isinstance(raw_child_ids, list):
            child_candidates = raw_child_ids
        elif isinstance(raw_child_ids, tuple):
            child_candidates = list(raw_child_ids)
        else:
            child_candidates = []
        child_ids: set[str] = set()
        for item in child_candidates:
            child_id = str(item or "").strip()
            if child_id:
                child_ids.add(child_id)
        if child_ids:
            normalized[file_id] = child_ids
    return normalized


async def run_search_and_group_batch(
    state: RetrievalBriefState,
    *,
    excluded_file_ids: set[str] | None = None,
    allowed_file_ids_override: set[str] | None = None,
    excluded_child_chunk_ids_by_file: dict[str, set[str]] | None = None,
    batch_id: int | None = None,
) -> dict[str, Any]:
    """
    The endpoint that orchestration node calls to run a reusable search/group batch runner with optional file-id exclusion.
    """
    print(f"[Agentic Modification - Node 2] Running search/group batch with batch_id={batch_id}...")
    #TODO: We can remove the normalise here since it is already nromalised at node 1 
    lexical_anchors = _normalize_anchors(state.get("lexical_anchors"))
    semantic_anchors = _normalize_anchors(state.get("semantic_anchors"))

    # Normalise and get all the excluded file IDs, child chunk IDs and allowed file IDs for this batch
    excluded_ids = _normalize_excluded_file_ids(excluded_file_ids)
    allowed_file_ids = (
        _normalize_allowed_file_ids(allowed_file_ids_override)
        if allowed_file_ids_override is not None
        else _normalize_allowed_file_ids(state.get("file_ids"))
    )
    user_id = str(state.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("user_id is required in agentic retrieval state.")
    excluded_child_ids_by_file = _normalize_excluded_child_chunk_ids_by_file(excluded_child_chunk_ids_by_file)
    resolved_batch_id = int(batch_id or 1)

    async def _run_lexical_query(anchor: str) -> tuple[str, list[dict[str, Any]], str]:
        """
        Internal function to run a lexical search query for a given anchor, applying overfetching and post-filtering based on excluded file IDs.
        """
        hits: list[dict[str, Any]] = []
        seen_child_ids: set[str] = set()
        used_overfetch = False

        # TODO: Not sure if we still need this multiplier
        for index, multiplier in enumerate(SEARCH_EXCLUSION_OVERFETCH_MULTIPLIERS):
            request_k = max(SEARCH_TOP_K, SEARCH_TOP_K * int(multiplier))
            print(f"[Agentic Modification - Node 2] Running lexical search with request_k={request_k}...")

            # Call the lexical search with the current level of overfecting and server-side exclusion
            lexical_search_kwargs: dict[str, Any] = {
                "query": anchor,
                "top_k": request_k,
                "user_id": user_id,
                "excluded_file_ids": excluded_ids,
            }
            if allowed_file_ids:
                lexical_search_kwargs["included_file_ids"] = allowed_file_ids
            rows = await vector_search._run_lexical_search(**lexical_search_kwargs)
            if index > 0:
                used_overfetch = True

            for row in rows:
                metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                child_chunk_id = _resolve_lexical_child_chunk_id(row, query=anchor)
                if child_chunk_id in seen_child_ids:
                    continue

                file_id, file_name = _extract_file_metadata(metadata)
                if allowed_file_ids and file_id not in allowed_file_ids:
                    continue
                if file_id in excluded_ids:
                    continue
                if child_chunk_id in excluded_child_ids_by_file.get(file_id, set()):
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
        """
        Internal function to run a semantic search query for a given anchor, applying overfetching and post-filtering based on excluded file IDs and child chunk IDs.
        """
        hits: list[dict[str, Any]] = []
        seen_child_ids: set[str] = set()
        used_overfetch = False

        for index, multiplier in enumerate(SEARCH_EXCLUSION_OVERFETCH_MULTIPLIERS):
            request_k = max(SEARCH_TOP_K, SEARCH_TOP_K * int(multiplier))
            semantic_search_kwargs: dict[str, Any] = {
                "query": anchor,
                "top_k": request_k,
                "user_id": user_id,
                "excluded_file_ids": excluded_ids,
            }
            if allowed_file_ids:
                semantic_search_kwargs["included_file_ids"] = allowed_file_ids
            items = await vector_search._run_semantic_search(**semantic_search_kwargs)
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
                if allowed_file_ids and file_id not in allowed_file_ids:
                    continue
                if file_id in excluded_ids:
                    continue
                if child_chunk_id in excluded_child_ids_by_file.get(file_id, set()):
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

    # Run lexical and semantic query groups in parallel to reduce end-to-end Node-2 latency.
    lexical_results, semantic_results = await asyncio.gather(
        asyncio.gather(*[_run_lexical_query(anchor) for anchor in lexical_anchors]),
        asyncio.gather(*[_run_semantic_query(anchor) for anchor in semantic_anchors]),
    )

    lexical_hits_by_query = {anchor: hits for anchor, hits, _ in lexical_results}
    semantic_hits_by_query = {anchor: hits for anchor, hits, _ in semantic_results}
    lexical_query_modes = {anchor: mode for anchor, _, mode in lexical_results}
    semantic_query_modes = {anchor: mode for anchor, _, mode in semantic_results}

    # Merge lexical and semantic streams so downstream ranking logic is source-agnostic.
    all_hits: list[dict[str, Any]] = []
    for hits in lexical_hits_by_query.values():
        all_hits.extend(hits)
    for hits in semantic_hits_by_query.values():
        all_hits.extend(hits)

    # Two aggregation views:
    # - child_agg: per child chunk retrieval evidence
    # - file_agg: per file retrieval evidence
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

        # TODO: We can remove this total_hit_count in the dict since it is just the sum of lexical and semantic hit count which we already have
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

    # TODO: We can remove the avg_semantic_score in the dict as well as stong_signal_chunk
    # The reason we can remove strong_signal chunk is because the current version, all the fecthed chunks will get their parent chunks and send to node 3 for relevance evaluation
    # Hence no reason for us to have strong_signal_chunk anymore
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

    # Sort the child chunks by strong signal, then total hit count, then child chunk ID for deterministic tie-breaking
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

    # "Strong signal" references are explicit debug artifacts used by later decision nodes.
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
        "allowed_file_ids": sorted(allowed_file_ids),
        "excluded_file_ids": sorted(excluded_ids),
        "excluded_child_chunk_ids_by_file": [
            {
                "file_id": file_id,
                "child_chunk_ids": sorted(child_ids),
            }
            for file_id, child_ids in sorted(excluded_child_ids_by_file.items())
        ],
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
            "allowed_file_id_count": len(allowed_file_ids),
            "allowed_file_ids": sorted(allowed_file_ids) if allowed_file_ids else "none",
            "excluded_file_id_count": len(excluded_ids),
            "excluded_file_ids": sorted(excluded_ids) if excluded_ids else "none",
            "excluded_child_chunk_id_count": sum(len(child_ids) for child_ids in excluded_child_ids_by_file.values()),
            "fetched_file_ids": fetched_file_ids if fetched_file_ids else "none",
            "strong_signal_chunk_count": sum(1 for item in child_results if item["strong_signal_chunk"]),
            "strong_signal_file_count": sum(1 for item in file_results if item["strong_signal_file"]),
            "strong_signal_chunks": strong_signal_chunk_refs if strong_signal_chunk_refs else "none",
            "strong_signal_files": strong_signal_file_refs if strong_signal_file_refs else "none",
        },
    }


async def search_and_group_node(state: RetrievalBriefState) -> dict:
    """
    Node 2 wrapper: run one search/group batch with no exclusions.
    
    It is not used by the orchestration node
    """
    print("[Agentic Modification - Node 2] Running search and grouping...")
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
