"""Vector and lexical search helpers for Agent v2."""
from __future__ import annotations

import hashlib
from typing import Any

from langchain_core.documents import Document

from ..shared.constants import SEMANTIC_FILE_FILTER_FALLBACK_K_STEPS
from ..shared.search_utils import (
    _extract_file_metadata,
    _resolve_semantic_child_chunk_id,
    _safe_float,
)


def _normalize_excluded_file_ids(raw_excluded_file_ids: Any) -> list[str]:
    if isinstance(raw_excluded_file_ids, set):
        candidates = list(raw_excluded_file_ids)
    elif isinstance(raw_excluded_file_ids, list):
        candidates = raw_excluded_file_ids
    elif isinstance(raw_excluded_file_ids, tuple):
        candidates = list(raw_excluded_file_ids)
    else:
        candidates = []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _extract_doc_file_id(doc_candidate: Document) -> str:
    metadata = doc_candidate.metadata if isinstance(doc_candidate.metadata, dict) else {}
    file_id, _ = _extract_file_metadata(metadata)
    return file_id


def _normalize_excluded_child_chunk_ids(raw_excluded_child_chunk_ids: Any) -> set[str]:
    if isinstance(raw_excluded_child_chunk_ids, set):
        candidates = list(raw_excluded_child_chunk_ids)
    elif isinstance(raw_excluded_child_chunk_ids, list):
        candidates = raw_excluded_child_chunk_ids
    elif isinstance(raw_excluded_child_chunk_ids, tuple):
        candidates = list(raw_excluded_child_chunk_ids)
    else:
        candidates = []

    normalized: set[str] = set()
    for item in candidates:
        value = str(item or "").strip()
        if value:
            normalized.add(value)
    return normalized


def _ensure_retrieval_cache(cache: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(cache, dict):
        cache = {}
    cache.setdefault("semantic_file_search", {})
    cache.setdefault("parent_chunks", {})
    cache.setdefault(
        "stats",
        {
            "semantic_search_hits": 0,
            "semantic_search_misses": 0,
            "parent_chunk_hits": 0,
            "parent_chunk_misses": 0,
        },
    )
    return cache


def _snapshot_retrieval_cache_stats(cache: dict[str, Any] | None) -> dict[str, int]:
    cache_obj = _ensure_retrieval_cache(cache)
    stats = cache_obj.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    return {
        "semantic_search_hits": int(stats.get("semantic_search_hits", 0) or 0),
        "semantic_search_misses": int(stats.get("semantic_search_misses", 0) or 0),
        "parent_chunk_hits": int(stats.get("parent_chunk_hits", 0) or 0),
        "parent_chunk_misses": int(stats.get("parent_chunk_misses", 0) or 0),
    }


def _semantic_file_search_cache_key(
    *,
    file_id: str,
    query: str,
    top_k: int,
    excluded_child_chunk_ids: set[str],
) -> str:
    excluded_sorted = sorted(excluded_child_chunk_ids)
    digest = hashlib.sha1("\n".join(excluded_sorted).encode("utf-8")).hexdigest() if excluded_sorted else "none"
    return f"{file_id}|{query}|{int(top_k)}|{digest}"


def _filter_semantic_items_for_file(
    items: list[tuple[Document, float]],
    *,
    normalized_file_id: str,
    excluded_child_chunk_ids: set[str],
    top_k: int,
) -> list[tuple[Document, float]]:
    normalized_items: list[tuple[Document, float]] = []
    seen_child_ids: set[str] = set(excluded_child_chunk_ids)

    for item in items:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        doc_candidate = item[0]
        if not isinstance(doc_candidate, Document):
            continue

        metadata = doc_candidate.metadata if isinstance(doc_candidate.metadata, dict) else {}
        row_file_id, _ = _extract_file_metadata(metadata)
        if normalized_file_id and row_file_id != normalized_file_id:
            continue

        try:
            child_chunk_id = _resolve_semantic_child_chunk_id(doc_candidate, query="")
        except ValueError:
            continue
        if child_chunk_id in seen_child_ids:
            continue
        seen_child_ids.add(child_chunk_id)

        score = _safe_float(item[1])
        if score is None:
            continue
        normalized_items.append((doc_candidate, score))
        if len(normalized_items) >= top_k:
            break

    return normalized_items


async def _run_lexical_search(
    query: str,
    top_k: int,
    *,
    excluded_file_ids: set[str] | list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    from app.vectordb.vectordb import lexical_search_child_chunks

    normalized_excluded_file_ids = _normalize_excluded_file_ids(excluded_file_ids)
    return await lexical_search_child_chunks(
        query=query,
        top_k=top_k,
        excluded_file_ids=normalized_excluded_file_ids,
    )


async def _run_semantic_search(
    query: str,
    top_k: int,
    *,
    excluded_file_ids: set[str] | list[str] | tuple[str, ...] | None = None,
) -> list[tuple[Document, float]]:
    from app.vectordb import vectordb as vectordb_module

    normalized_excluded_file_ids = _normalize_excluded_file_ids(excluded_file_ids)
    vector_store = vectordb_module.VECTOR_STORE

    if not normalized_excluded_file_ids:
        return await vector_store.asimilarity_search_with_score(query, k=top_k)

    filter_candidates = [
        {"file_metadata.file_id": {"$nin": normalized_excluded_file_ids}},
        {"metadata.file_metadata.file_id": {"$nin": normalized_excluded_file_ids}},
    ]

    for filter_doc in filter_candidates:
        try:
            items = await vector_store.asimilarity_search_with_score(
                query,
                k=top_k,
                filter=filter_doc,
            )
        except TypeError:
            break
        except Exception:
            continue

        if not isinstance(items, list):
            continue

        normalized_items: list[tuple[Document, float]] = []
        leaked_excluded = False
        for item in items:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            doc_candidate = item[0]
            if not isinstance(doc_candidate, Document):
                continue
            file_id = _extract_doc_file_id(doc_candidate)
            if file_id in normalized_excluded_file_ids:
                leaked_excluded = True
                continue
            normalized_items.append(item)

        if leaked_excluded:
            continue
        return normalized_items[:top_k]

    # Fallback: run without server-side exclusion and apply client-side exclusion.
    items = await vector_store.asimilarity_search_with_score(query, k=top_k)
    if not isinstance(items, list):
        return []

    filtered_items: list[tuple[Document, float]] = []
    for item in items:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        doc_candidate = item[0]
        if not isinstance(doc_candidate, Document):
            continue
        file_id = _extract_doc_file_id(doc_candidate)
        if file_id in normalized_excluded_file_ids:
            continue
        filtered_items.append(item)

    return filtered_items[:top_k]


async def _run_semantic_search_for_file(
    query: str,
    file_id: str,
    top_k: int,
    *,
    excluded_child_chunk_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    cache: dict[str, Any] | None = None,
) -> tuple[list[tuple[Document, float]], str]:
    from app.vectordb import vectordb as vectordb_module

    normalized_file_id = str(file_id or "").strip()
    if not normalized_file_id:
        return [], "missing_file_id"

    normalized_top_k = max(int(top_k or 0), 1)
    normalized_excluded_child_chunk_ids = _normalize_excluded_child_chunk_ids(excluded_child_chunk_ids)
    cache_obj = _ensure_retrieval_cache(cache)
    semantic_cache = cache_obj.get("semantic_file_search")
    if not isinstance(semantic_cache, dict):
        semantic_cache = {}
        cache_obj["semantic_file_search"] = semantic_cache

    cache_key = _semantic_file_search_cache_key(
        file_id=normalized_file_id,
        query=str(query or ""),
        top_k=normalized_top_k,
        excluded_child_chunk_ids=normalized_excluded_child_chunk_ids,
    )
    cached_entry = semantic_cache.get(cache_key)
    if isinstance(cached_entry, dict):
        stats = cache_obj.get("stats")
        if isinstance(stats, dict):
            stats["semantic_search_hits"] = int(stats.get("semantic_search_hits", 0) or 0) + 1
        cached_items = cached_entry.get("items")
        cached_mode = str(cached_entry.get("search_mode") or "cache")
        if isinstance(cached_items, list):
            return cached_items, cached_mode

    stats = cache_obj.get("stats")
    if isinstance(stats, dict):
        stats["semantic_search_misses"] = int(stats.get("semantic_search_misses", 0) or 0) + 1

    vector_store = vectordb_module.VECTOR_STORE
    filter_candidates = [
        {"file_metadata.file_id": normalized_file_id},
        {"metadata.file_metadata.file_id": normalized_file_id},
        {"file_id": normalized_file_id},
    ]

    for filter_doc in filter_candidates:
        try:
            filtered = await vector_store.asimilarity_search_with_score(
                query,
                k=normalized_top_k,
                filter=filter_doc,
            )
            if isinstance(filtered, list):
                normalized_filtered = _filter_semantic_items_for_file(
                    filtered,
                    normalized_file_id=normalized_file_id,
                    excluded_child_chunk_ids=normalized_excluded_child_chunk_ids,
                    top_k=normalized_top_k,
                )
                if normalized_filtered:
                    semantic_cache[cache_key] = {
                        "items": normalized_filtered,
                        "search_mode": "native_filter",
                    }
                    return normalized_filtered, "native_filter"
        except TypeError:
            break
        except Exception:
            continue

    collected: list[tuple[Document, float]] = []
    seen_child_ids: set[str] = set(normalized_excluded_child_chunk_ids)
    for fallback_k in SEMANTIC_FILE_FILTER_FALLBACK_K_STEPS:
        try:
            items = await _run_semantic_search(query=query, top_k=max(fallback_k, normalized_top_k))
        except Exception:
            continue

        filtered_items = _filter_semantic_items_for_file(
            items,
            normalized_file_id=normalized_file_id,
            excluded_child_chunk_ids=seen_child_ids,
            top_k=normalized_top_k,
        )
        for doc_candidate, score in filtered_items:
            try:
                child_chunk_id = _resolve_semantic_child_chunk_id(doc_candidate, query=query)
            except ValueError:
                continue
            if child_chunk_id in seen_child_ids:
                continue
            seen_child_ids.add(child_chunk_id)
            collected.append((doc_candidate, score))
            if len(collected) >= normalized_top_k:
                semantic_cache[cache_key] = {
                    "items": collected[:normalized_top_k],
                    "search_mode": "fallback_post_filter",
                }
                return collected[:normalized_top_k], "fallback_post_filter"

    semantic_cache[cache_key] = {
        "items": collected[:normalized_top_k],
        "search_mode": "fallback_post_filter",
    }
    return collected[:normalized_top_k], "fallback_post_filter"


async def _fetch_parent_chunks(
    parent_ids: list[str],
    *,
    cache: dict[str, Any] | None = None,
) -> list[dict[str, Any] | None]:
    from app.vectordb import vectordb as vectordb_module

    normalized_parent_ids: list[str] = []
    for parent_id in parent_ids:
        value = str(parent_id or "").strip()
        if value:
            normalized_parent_ids.append(value)
    if not normalized_parent_ids:
        return []

    cache_obj = _ensure_retrieval_cache(cache)
    parent_cache = cache_obj.get("parent_chunks")
    if not isinstance(parent_cache, dict):
        parent_cache = {}
        cache_obj["parent_chunks"] = parent_cache

    missing_parent_ids: list[str] = []
    for parent_id in normalized_parent_ids:
        if parent_id not in parent_cache:
            missing_parent_ids.append(parent_id)

    stats = cache_obj.get("stats")
    if isinstance(stats, dict):
        stats["parent_chunk_hits"] = int(stats.get("parent_chunk_hits", 0) or 0) + (
            len(normalized_parent_ids) - len(missing_parent_ids)
        )
        stats["parent_chunk_misses"] = int(stats.get("parent_chunk_misses", 0) or 0) + len(missing_parent_ids)

    if missing_parent_ids:
        rows = await vectordb_module.PARENT_STORE.amget(missing_parent_ids)
        rows_list = rows if isinstance(rows, list) else []
        for parent_id, raw_doc in zip(missing_parent_ids, rows_list):
            parent_cache[parent_id] = raw_doc if isinstance(raw_doc, dict) else None
        if len(rows_list) < len(missing_parent_ids):
            for parent_id in missing_parent_ids[len(rows_list):]:
                parent_cache[parent_id] = None

    return [parent_cache.get(parent_id) for parent_id in normalized_parent_ids]
