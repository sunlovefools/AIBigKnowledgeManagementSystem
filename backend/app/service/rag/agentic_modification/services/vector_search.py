"""Vector and lexical search helpers for Agentic Modification."""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from langchain_core.documents import Document

from ..shared.constants import SEMANTIC_FILE_FILTER_FALLBACK_K_STEPS
from ..shared.search_utils import (
    _extract_file_metadata,
    _extract_parent_chunk_number,
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


def _normalize_included_file_ids(raw_included_file_ids: Any) -> list[str]:
    if isinstance(raw_included_file_ids, set):
        candidates = list(raw_included_file_ids)
    elif isinstance(raw_included_file_ids, list):
        candidates = raw_included_file_ids
    elif isinstance(raw_included_file_ids, tuple):
        candidates = list(raw_included_file_ids)
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


def _extract_row_file_id(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    file_id, _ = _extract_file_metadata(metadata)
    return file_id


def _normalize_semantic_raw_items(raw_items: Any) -> list[tuple[Document, float | None]]:
    if not isinstance(raw_items, list):
        return []

    normalized: list[tuple[Document, float | None]] = []
    for item in raw_items:
        if isinstance(item, Document):
            normalized.append((item, None))
            continue
        if not isinstance(item, tuple) or len(item) < 1:
            continue
        doc_candidate = item[0]
        if not isinstance(doc_candidate, Document):
            continue
        score = _safe_float(item[1]) if len(item) >= 2 else None
        normalized.append((doc_candidate, score))
    return normalized


async def _semantic_search(
    vector_store: Any,
    *,
    query: str,
    top_k: int,
    filter_doc: dict[str, Any] | None = None,
) -> list[tuple[Document, float | None]]:
    # Prefer score-agnostic search to avoid non-deterministic hybrid-score warnings.
    asimilarity_search = getattr(vector_store, "asimilarity_search", None)
    if callable(asimilarity_search):
        try:
            if filter_doc is None:
                raw_items = await asimilarity_search(query, k=top_k)
            else:
                raw_items = await asimilarity_search(query, k=top_k, filter=filter_doc)
            return _normalize_semantic_raw_items(raw_items)
        except TypeError:
            pass
        except Exception:
            pass

    asimilarity_search_with_score = getattr(vector_store, "asimilarity_search_with_score", None)
    if callable(asimilarity_search_with_score):
        if filter_doc is None:
            raw_items = await asimilarity_search_with_score(query, k=top_k)
        else:
            raw_items = await asimilarity_search_with_score(query, k=top_k, filter=filter_doc)
        return _normalize_semantic_raw_items(raw_items)

    return []


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
    """
    Ensure the retrieval cache is a well-formed dictionary with expected keys and default values.
    """
    if not isinstance(cache, dict):
        cache = {}
    cache.setdefault("semantic_file_search", {})
    cache.setdefault("parent_chunks", {})
    cache.setdefault("parent_chunks_by_file_chunk_number", {})
    cache.setdefault(
        "stats",
        {
            "semantic_search_hits": 0,
            "semantic_search_misses": 0,
            "parent_chunk_hits": 0,
            "parent_chunk_misses": 0,
            "file_chunk_lookup_hits": 0,
            "file_chunk_lookup_misses": 0,
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
        "file_chunk_lookup_hits": int(stats.get("file_chunk_lookup_hits", 0) or 0),
        "file_chunk_lookup_misses": int(stats.get("file_chunk_lookup_misses", 0) or 0),
    }


def _normalize_chunk_number(raw_value: Any) -> int | None:
    """Normalize chunk number from various raw formats to an integer, or return None if invalid."""
    if isinstance(raw_value, bool):
        return None
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, float):
        return int(raw_value)
    if isinstance(raw_value, str):
        value = raw_value.strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _cache_parent_chunk_row(
    cache_obj: dict[str, Any],
    *,
    parent_id: str,
    raw_doc: dict[str, Any] | None,
) -> None:
    """
    Cache the raw document of a parent chunk by its ID, and also index it by file ID and chunk number for efficient lookup.
    """
    parent_cache = cache_obj.get("parent_chunks")
    if not isinstance(parent_cache, dict):
        parent_cache = {}
        cache_obj["parent_chunks"] = parent_cache

    normalized_parent_id = str(parent_id or "").strip()
    if not normalized_parent_id:
        return

    parent_cache[normalized_parent_id] = raw_doc if isinstance(raw_doc, dict) else None
    if not isinstance(raw_doc, dict):
        return

    metadata = raw_doc.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    file_id, _ = _extract_file_metadata(metadata)
    chunk_number = _extract_parent_chunk_number(metadata)
    if file_id == "unknown" or not isinstance(chunk_number, int):
        return

    file_chunk_index = cache_obj.get("parent_chunks_by_file_chunk_number")
    if not isinstance(file_chunk_index, dict):
        file_chunk_index = {}
        cache_obj["parent_chunks_by_file_chunk_number"] = file_chunk_index

    per_file_index = file_chunk_index.get(file_id)
    if not isinstance(per_file_index, dict):
        per_file_index = {}
        file_chunk_index[file_id] = per_file_index

    per_file_index[chunk_number] = normalized_parent_id


def _build_parent_chunk_payload(
    *,
    parent_id: str,
    raw_doc: dict[str, Any],
    fallback_file_id: str | None = None,
) -> dict[str, Any] | None:
    """
    Build a standardized payload for a parent chunk using its raw document, extracting and normalizing key metadata fields.
    """
    metadata = raw_doc.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    file_id, file_name = _extract_file_metadata(metadata)
    chunk_number = _extract_parent_chunk_number(metadata)
    if not isinstance(chunk_number, int):
        return None
    if file_id == "unknown":
        file_id = str(fallback_file_id or "").strip() or "unknown"
    return {
        "parent_id": str(parent_id or "").strip(),
        "chunk_number": chunk_number,
        "page_content": str(raw_doc.get("page_content") or ""),
        "file_id": file_id,
        "file_name": file_name,
    }


def _lookup_cached_parent_chunk_payload(
    *,
    cache_obj: dict[str, Any],
    file_id: str,
    chunk_number: int,
) -> dict[str, Any] | None:
    """
    Lookup the cached parent chunk payload for a given file ID and chunk number, returning None if not found or if the cache is malformed.
    """
    file_chunk_index = cache_obj.get("parent_chunks_by_file_chunk_number")
    if not isinstance(file_chunk_index, dict):
        return None
    per_file_index = file_chunk_index.get(file_id)
    if not isinstance(per_file_index, dict):
        return None
    parent_id = str(per_file_index.get(chunk_number) or "").strip()
    if not parent_id:
        return None

    parent_cache = cache_obj.get("parent_chunks")
    if not isinstance(parent_cache, dict):
        return None
    raw_doc = parent_cache.get(parent_id)
    if not isinstance(raw_doc, dict):
        return None

    payload = _build_parent_chunk_payload(
        parent_id=parent_id,
        raw_doc=raw_doc,
        fallback_file_id=file_id,
    )
    if not isinstance(payload, dict):
        return None
    if str(payload.get("file_id") or "").strip() != file_id:
        return None
    return payload


def _semantic_file_search_cache_key(
    *,
    file_id: str,
    query: str,
    top_k: int,
    excluded_child_chunk_ids: set[str],
) -> str:
    """
    Build a cache key for semantic file search results based on the file ID, query, top_k, and excluded child chunk IDs. 
    The excluded child chunk IDs are hashed to avoid excessively long keys.
    """
    excluded_sorted = sorted(excluded_child_chunk_ids)
    digest = hashlib.sha1("\n".join(excluded_sorted).encode("utf-8")).hexdigest() if excluded_sorted else "none"
    return f"{file_id}|{query}|{int(top_k)}|{digest}"


def _filter_semantic_items_for_file(
    items: list[tuple[Document, float | None]],
    *,
    normalized_file_id: str,
    excluded_child_chunk_ids: set[str],
    top_k: int,
) -> list[tuple[Document, float | None]]:
    """
    Filter and normalize raw semantic search items for a specific file, applying client-side exclusion of child chunk IDs and limiting to top_k results.
    """
    normalized_items: list[tuple[Document, float | None]] = []
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

        score = _safe_float(item[1]) if len(item) >= 2 else None
        normalized_items.append((doc_candidate, score))
        if len(normalized_items) >= top_k:
            break

    return normalized_items


async def _run_lexical_search(
    query: str,
    top_k: int,
    *,
    excluded_file_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    included_file_ids: set[str] | list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """
    A wrapper to run lexical search with overfetching and post-filtering based on excluded file IDs. 
    It first attempts to run the search with server-side exclusion, and if that fails or returns leaked excluded IDs,
    it falls back to running without server-side exclusion and applying client-side filtering.
    """
    from app.vectordb.vectordb import lexical_search_child_chunks

    # TODO: can we remove this normlaise again?
    normalized_excluded_file_ids = _normalize_excluded_file_ids(excluded_file_ids)
    normalized_included_file_ids = _normalize_included_file_ids(included_file_ids)
    rows = await lexical_search_child_chunks(
        query=query,
        top_k=top_k,
        excluded_file_ids=normalized_excluded_file_ids,
        included_file_ids=normalized_included_file_ids,
    )

    if not normalized_included_file_ids:
        return rows

    included_set = set(normalized_included_file_ids)
    filtered_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        file_id = _extract_row_file_id(row)
        if file_id not in included_set:
            continue
        filtered_rows.append(row)
    return filtered_rows[:top_k]


async def _run_semantic_search(
    query: str,
    top_k: int,
    *,
    excluded_file_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    included_file_ids: set[str] | list[str] | tuple[str, ...] | None = None,
) -> list[tuple[Document, float | None]]:
    """
    A wrapper to run semantic search with optional server-side exclusion of file IDs. It first attempts to run the search with server-side exclusion,
    and if that fails or returns leaked excluded IDs, it falls back to running without server-side exclusion and applying client-side filtering.
    """
    from app.vectordb import vectordb as vectordb_module

    normalized_excluded_file_ids = _normalize_excluded_file_ids(excluded_file_ids)
    normalized_included_file_ids = _normalize_included_file_ids(included_file_ids)
    included_set = set(normalized_included_file_ids)
    vector_store = vectordb_module.VECTOR_STORE

    if not normalized_excluded_file_ids:
        items = await _semantic_search(
            vector_store,
            query=query,
            top_k=top_k,
            filter_doc=None,
        )
        if not normalized_included_file_ids:
            return items

        filtered: list[tuple[Document, float | None]] = []
        for item in items:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            doc_candidate = item[0]
            if not isinstance(doc_candidate, Document):
                continue
            file_id = _extract_doc_file_id(doc_candidate)
            if file_id not in included_set:
                continue
            filtered.append(item)
        return filtered[:top_k]

    filter_candidates = [
        {"file_metadata.file_id": {"$nin": normalized_excluded_file_ids}},
    ]

    for filter_doc in filter_candidates:
        try:
            items = await _semantic_search(
                vector_store,
                query=query,
                top_k=top_k,
                filter_doc=filter_doc,
            )
        except Exception:
            continue

        normalized_items: list[tuple[Document, float | None]] = []
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
            if normalized_included_file_ids and file_id not in included_set:
                continue
            normalized_items.append(item)

        if leaked_excluded:
            continue
        if normalized_items:
            return normalized_items[:top_k]

    # Fallback: run without server-side exclusion and apply client-side exclusion.
    items = await _semantic_search(
        vector_store,
        query=query,
        top_k=top_k,
        filter_doc=None,
    )

    filtered_items: list[tuple[Document, float | None]] = []
    for item in items:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        doc_candidate = item[0]
        if not isinstance(doc_candidate, Document):
            continue
        file_id = _extract_doc_file_id(doc_candidate)
        if file_id in normalized_excluded_file_ids:
            continue
        if normalized_included_file_ids and file_id not in included_set:
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
) -> tuple[list[tuple[Document, float | None]], str]:
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
    ]

    for filter_doc in filter_candidates:
        try:
            filtered = await _semantic_search(
                vector_store,
                query=query,
                top_k=normalized_top_k,
                filter_doc=filter_doc,
            )
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
        except Exception:
            continue

    collected: list[tuple[Document, float | None]] = []
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
            _cache_parent_chunk_row(
                cache_obj,
                parent_id=parent_id,
                raw_doc=raw_doc if isinstance(raw_doc, dict) else None,
            )
        if len(rows_list) < len(missing_parent_ids):
            for parent_id in missing_parent_ids[len(rows_list):]:
                parent_cache[parent_id] = None

    for parent_id in normalized_parent_ids:
        cached_row = parent_cache.get(parent_id)
        if isinstance(cached_row, dict):
            _cache_parent_chunk_row(
                cache_obj,
                parent_id=parent_id,
                raw_doc=cached_row,
            )

    return [parent_cache.get(parent_id) for parent_id in normalized_parent_ids]


async def _fetch_parent_chunks_for_file_chunk_numbers(
    file_id: str,
    chunk_numbers: list[int] | tuple[int, ...] | set[int],
    *,
    cache: dict[str, Any] | None = None,
) -> dict[int, dict[str, Any]]:
    normalized_file_id = str(file_id or "").strip()
    if not normalized_file_id:
        return {}

    normalized_chunk_numbers: list[int] = []
    seen: set[int] = set()
    for raw_chunk_number in list(chunk_numbers):
        chunk_number = _normalize_chunk_number(raw_chunk_number)
        if chunk_number is None:
            continue
        if chunk_number in seen:
            continue
        seen.add(chunk_number)
        normalized_chunk_numbers.append(chunk_number)
    if not normalized_chunk_numbers:
        return {}

    cache_obj = _ensure_retrieval_cache(cache)
    stats = cache_obj.get("stats")
    if not isinstance(stats, dict):
        stats = {}
        cache_obj["stats"] = stats

    resolved: dict[int, dict[str, Any]] = {}
    unresolved_chunk_numbers: list[int] = []

    for chunk_number in normalized_chunk_numbers:
        cached_payload = _lookup_cached_parent_chunk_payload(
            cache_obj=cache_obj,
            file_id=normalized_file_id,
            chunk_number=chunk_number,
        )
        if isinstance(cached_payload, dict):
            resolved[chunk_number] = cached_payload
        else:
            unresolved_chunk_numbers.append(chunk_number)

    stats["file_chunk_lookup_hits"] = int(stats.get("file_chunk_lookup_hits", 0) or 0) + len(resolved)
    stats["file_chunk_lookup_misses"] = int(stats.get("file_chunk_lookup_misses", 0) or 0) + len(
        unresolved_chunk_numbers
    )

    if not unresolved_chunk_numbers:
        return resolved

    from app.vectordb import vectordb as vectordb_module

    def _query_parent_rows_by_file_and_chunk_numbers() -> list[dict[str, Any]]:
        """
        Internal helper to query the parent store for row matching the specified file ID and any of the unresolved chunk numbers. 
        This is run in a separate thread to avoid blocking the event loop.
        """
        collection = vectordb_module.PARENT_STORE.collection
        filter_doc = {
            "value.metadata.file_metadata.file_id": normalized_file_id,
            "value.metadata.parent_chunk_metadata.parent_chunk_number": {"$in": unresolved_chunk_numbers},
        }
        projection_doc = {"_id": True, "value": True}
        rows: list[dict[str, Any]] = []
        for row in collection.find(filter_doc, projection=projection_doc):
            if isinstance(row, dict):
                rows.append(row)
        return rows

    rows = await asyncio.to_thread(_query_parent_rows_by_file_and_chunk_numbers)
    for row in rows:
        parent_id = str(row.get("_id") or "").strip()
        raw_doc = row.get("value")
        if not parent_id:
            continue

        _cache_parent_chunk_row(
            cache_obj,
            parent_id=parent_id,
            raw_doc=raw_doc if isinstance(raw_doc, dict) else None,
        )
        if not isinstance(raw_doc, dict):
            continue
        payload = _build_parent_chunk_payload(
            parent_id=parent_id,
            raw_doc=raw_doc,
            fallback_file_id=normalized_file_id,
        )
        if not isinstance(payload, dict):
            continue
        payload_file_id = str(payload.get("file_id") or "").strip()
        payload_chunk_number = payload.get("chunk_number")
        if payload_file_id != normalized_file_id or not isinstance(payload_chunk_number, int):
            continue
        if payload_chunk_number in unresolved_chunk_numbers:
            resolved[payload_chunk_number] = payload

    return resolved


async def _get_parent_chunks_for_file_range(
    file_id: str,
    start_chunk_number: int,
    end_chunk_number: int,
    *,
    cache: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    """
    Retrieves parent chunk payloads for a specified range of chunk numbers within a file. 
    """
    normalized_start = _normalize_chunk_number(start_chunk_number)
    normalized_end = _normalize_chunk_number(end_chunk_number)
    if normalized_start is None or normalized_end is None:
        return None

    start = min(normalized_start, normalized_end)
    end = max(normalized_start, normalized_end)
    if end < start:
        return None

    requested_numbers = list(range(start, end + 1))
    if not requested_numbers:
        return None

    resolved_map = await _fetch_parent_chunks_for_file_chunk_numbers(
        file_id=file_id,
        chunk_numbers=requested_numbers,
        cache=cache,
    )
    if not resolved_map:
        return None

    ordered_payloads: list[dict[str, Any]] = []
    for chunk_number in requested_numbers:
        payload = resolved_map.get(chunk_number)
        if isinstance(payload, dict):
            ordered_payloads.append(payload)

    return ordered_payloads or None


async def _get_surrounding_parent_chunks_for_file(
    file_id: str,
    chunk_number: int,
    *,
    cache: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    """
    Retrieves parent chunk payloads for a specified chunk number within a file, as well as a few chunks before and after it to provide context.
    """
    normalized_chunk_number = _normalize_chunk_number(chunk_number)
    if normalized_chunk_number is None:
        return None

    lower_numbers = [
        candidate
        for candidate in range(normalized_chunk_number - 3, normalized_chunk_number)
        if candidate >= 0
    ]
    upper_numbers = [
        candidate
        for candidate in range(normalized_chunk_number + 1, normalized_chunk_number + 4)
        if candidate >= 0
    ]
    requested_numbers = [*lower_numbers, *upper_numbers]
    if not requested_numbers:
        return None

    resolved_map = await _fetch_parent_chunks_for_file_chunk_numbers(
        file_id=file_id,
        chunk_numbers=requested_numbers,
        cache=cache,
    )
    if not resolved_map:
        return None

    ordered_payloads: list[dict[str, Any]] = []
    for number in requested_numbers:
        payload = resolved_map.get(number)
        if isinstance(payload, dict):
            ordered_payloads.append(payload)
    return ordered_payloads or None
