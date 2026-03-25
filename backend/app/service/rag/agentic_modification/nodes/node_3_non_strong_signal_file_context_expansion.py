"""Node 3: expand file context with semantic anchors (all candidate files).

Node 2 returns child-level evidence. Node 3 resolves those signals back to parent
chunks so Node 4 can classify editability at parent-chunk granularity.
"""
from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.documents import Document

from ..services import vector_search
from ..shared.constants import NON_STRONG_SIGNAL_FILE_TOP_K
from ..shared.logging import log_modification_agent_search_group
from ..shared.normalization import _normalize_anchors
from ..shared.search_utils import (
    _average_or_none,
    _build_parent_chunks_prompt_payload,
    _extract_parent_child_chunk_ids,
    _extract_file_metadata,
    _extract_parent_chunk_number,
    _parent_sort_key,
    _resolve_semantic_child_chunk_id,
    _resolve_semantic_parent_id,
    _safe_float,
)
from ..state.retrieval_brief_state import RetrievalBriefState


async def run_non_strong_signal_file_context_expansion_batch(
    state: RetrievalBriefState,
    *,
    search_group_result: dict[str, Any],
    batch_id: int | None = None,
) -> dict[str, Any]:
    """
    Entry point from orchestration node

    Reusable expansion batch runner across all candidate files.
    """
    files = search_group_result.get("files") if isinstance(search_group_result, dict) else []
    if not isinstance(files, list):
        files = []

    semantic_anchors = _normalize_anchors(state.get("semantic_anchors"))
    candidate_files = [item for item in files if isinstance(item, dict)]
    non_strong_signal_files = [
        item for item in files if isinstance(item, dict) and not bool(item.get("strong_signal_file", False))
    ]
    strong_signal_files = [
        item for item in files if isinstance(item, dict) and bool(item.get("strong_signal_file", False))
    ]
    resolved_batch_id = int(batch_id)
    retrieval_cache = vector_search._ensure_retrieval_cache(state.get("_retrieval_cache"))
    user_id = str(state.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("user_id is required in agentic retrieval state.")
    state["_retrieval_cache"] = retrieval_cache

    if not candidate_files or not semantic_anchors:
        return {
            "batch_id": resolved_batch_id,
            "queries": {
                "semantic_anchors": semantic_anchors,
            },
            "files": [],
            "run_summary": {
                "top_k_per_anchor_per_file": NON_STRONG_SIGNAL_FILE_TOP_K,
                "candidate_file_count": len(candidate_files),
                "candidate_non_strong_file_count": len(non_strong_signal_files),
                "candidate_strong_signal_file_count": len(strong_signal_files),
                "expanded_file_count": 0,
                "expanded_child_chunk_count": 0,
                "expanded_parent_chunk_count": 0,
                "error_count": 0,
                "cache_stats": vector_search._snapshot_retrieval_cache_stats(retrieval_cache),
            },
            "errors": "none",
            "_retrieval_cache": retrieval_cache,
        }

    async def _expand_one_file(file_item: dict[str, Any]) -> dict[str, Any]:
        """
        Internal function to expand context for a single file based on semantic anchors, returning the expanded child chunks and parent chunks with associated metadata.
        """
        # Expansion is isolated per file, which allows safe parallel fan-out.
        file_id = str(file_item.get("file_id") or "").strip() or "unknown"
        file_name = str(file_item.get("file_name") or "").strip() or "unknown"

        semantic_query_hits: dict[str, list[dict[str, Any]]] = {}
        semantic_query_modes: dict[str, str] = {}
        child_aggregate: dict[str, dict[str, Any]] = {}

        async def _run_semantic_anchor(anchor: str) -> tuple[str, list[tuple[Document, float | None]], str]:
            """Run a semantic search for a single anchor against the given file, returning the raw hits and the search mode used."""
            try:
                items, search_mode = await vector_search._run_semantic_search_for_file(
                    query=anchor,
                    file_id=file_id,
                    top_k=NON_STRONG_SIGNAL_FILE_TOP_K,
                    user_id=user_id,
                    excluded_child_chunk_ids=set(),
                    cache=retrieval_cache,
                )
            except TypeError:
                items, search_mode = await vector_search._run_semantic_search_for_file(
                    query=anchor,
                    file_id=file_id,
                    top_k=NON_STRONG_SIGNAL_FILE_TOP_K,
                    user_id=user_id,
                )
            return anchor, items, search_mode

        # Run all semantic anchors concurrently for this file.
        anchor_results = await asyncio.gather(
            *[_run_semantic_anchor(anchor) for anchor in semantic_anchors]
        )
        for anchor, items, search_mode in anchor_results:
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

        expanded_child_chunks: list[dict[str, Any]] = []
        for entry in child_aggregate.values():
            expanded_child_chunks.append(
                {
                    "child_chunk_id": entry["child_chunk_id"],
                    "parent_id": entry.get("parent_id"),
                    "file_id": entry["file_id"],
                    "file_name": entry["file_name"],
                    "query_hit_count": len(entry["queries"]),
                    "avg_semantic_score": _average_or_none(entry["scores"]),
                }
            )
        expanded_child_chunks.sort(
            key=lambda item: (
                -int(item.get("query_hit_count", 0)),
                -(item.get("avg_semantic_score") if isinstance(item.get("avg_semantic_score"), float) else -10**9),
                str(item.get("child_chunk_id", "")),
            )
        )

        # De-duplicate parent ids before parent document fetch.
        parent_ids: list[str] = []
        seen_parent_ids: set[str] = set()
        for child_item in expanded_child_chunks:
            parent_id = str(child_item.get("parent_id") or "").strip()
            if not parent_id or parent_id in seen_parent_ids:
                continue
            seen_parent_ids.add(parent_id)
            parent_ids.append(parent_id)

        parent_chunks: list[dict[str, Any]] = []
        if parent_ids:
            try:
                parent_rows = await vector_search._fetch_parent_chunks(
                    parent_ids,
                    user_id=user_id,
                    cache=retrieval_cache,
                )
            except TypeError:
                parent_rows = await vector_search._fetch_parent_chunks(parent_ids, user_id=user_id)
            for parent_id, raw_doc in zip(parent_ids, parent_rows):
                if not isinstance(raw_doc, dict):
                    continue
                metadata = raw_doc.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
                row_file_id, row_file_name = _extract_file_metadata(metadata)
                parent_chunks.append(
                    {
                        "parent_id": parent_id,
                        "chunk_number": _extract_parent_chunk_number(metadata),
                        "child_chunk_ids": _extract_parent_child_chunk_ids(metadata),
                        "page_content": str(raw_doc.get("page_content") or ""),
                        "file_id": row_file_id if row_file_id != "unknown" else file_id,
                        "file_name": row_file_name if row_file_name != "unknown" else file_name,
                    }
                )
            parent_chunks.sort(key=_parent_sort_key)

        return {
            "file_id": file_id,
            "file_name": file_name,
            "semantic_anchors": semantic_anchors,
            "semantic_query_modes": semantic_query_modes,
            "query_hits": {
                "semantic": semantic_query_hits,
            },
            "expanded_child_chunks": expanded_child_chunks,
            "parent_chunks": parent_chunks,
            "parent_chunks_prompt_payload": _build_parent_chunks_prompt_payload(parent_chunks),
        }

    # Expand every candidate file in parallel to reduce Node 3 latency.
    expanded_file_results_raw = await asyncio.gather(
        *[_expand_one_file(file_item) for file_item in candidate_files],
        return_exceptions=True,
    )

    expanded_file_results: list[dict[str, Any]] = []
    expansion_errors: list[dict[str, str]] = []
    for file_item, result in zip(candidate_files, expanded_file_results_raw):
        if isinstance(result, Exception):
            expansion_errors.append(
                {
                    "file_id": str(file_item.get("file_id") or "unknown"),
                    "file_name": str(file_item.get("file_name") or "unknown"),
                    "error": str(result),
                }
            )
            continue
        expanded_file_results.append(result)

    return {
        "batch_id": resolved_batch_id,
        "queries": {
            "semantic_anchors": semantic_anchors,
        },
        "files": expanded_file_results,
        "run_summary": {
            "top_k_per_anchor_per_file": NON_STRONG_SIGNAL_FILE_TOP_K,
            "candidate_file_count": len(candidate_files),
            "candidate_non_strong_file_count": len(non_strong_signal_files),
            "candidate_strong_signal_file_count": len(strong_signal_files),
            "expanded_file_count": len(expanded_file_results),
            "expanded_child_chunk_count": sum(
                len(file_item.get("expanded_child_chunks", []))
                for file_item in expanded_file_results
            ),
            "expanded_parent_chunk_count": sum(
                len(file_item.get("parent_chunks", []))
                for file_item in expanded_file_results
            ),
            "error_count": len(expansion_errors),
            "cache_stats": vector_search._snapshot_retrieval_cache_stats(retrieval_cache),
        },
        "errors": expansion_errors if expansion_errors else "none",
        "_retrieval_cache": retrieval_cache,
    }


async def non_strong_signal_file_context_expansion_node(state: RetrievalBriefState) -> dict:
    """Node 3 wrapper: run one expansion batch across all candidate files."""
    print("[Agentic Modification - Node 3] Expanding context for candidate files...")
    run_id = state.get("run_id")
    node2_result = state.get("node2_search_group_result", {})
    semantic_anchors = _normalize_anchors(state.get("semantic_anchors"))
    batch_id = int(node2_result.get("batch_id", 1)) if isinstance(node2_result, dict) else 1

    try:
        node_result = await run_non_strong_signal_file_context_expansion_batch(
            state,
            search_group_result=node2_result if isinstance(node2_result, dict) else {},
            batch_id=batch_id,
        )
        log_modification_agent_search_group(
            run_id=run_id,
            step="non_strong_signal_file_context_expansion",
            payload=node_result,
        )
        return {
            "node3_non_strong_signal_file_context_expansion_result": node_result,
        }
    except Exception as error:
        error_message = f"non_strong_signal_file_context_expansion node failed: {error}"
        print(error_message)
        log_modification_agent_search_group(
            run_id=run_id,
            step="non_strong_signal_file_context_expansion",
            payload={
                "queries": {
                    "semantic_anchors": semantic_anchors,
                },
                "error": error_message,
            },
        )
        return {
            "error": error_message,
        }
