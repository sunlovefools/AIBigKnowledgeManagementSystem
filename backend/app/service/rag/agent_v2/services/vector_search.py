"""Vector and lexical search helpers for Agent v2."""
from __future__ import annotations

from typing import Any

from langchain_core.documents import Document

from ..shared.constants import SEMANTIC_FILE_FILTER_FALLBACK_K_STEPS
from ..shared.search_utils import (
    _extract_file_metadata,
    _resolve_semantic_child_chunk_id,
    _safe_float,
)


async def _run_lexical_search(query: str, top_k: int) -> list[dict[str, Any]]:
    from app.vectordb.vectordb import lexical_search_child_chunks

    return await lexical_search_child_chunks(query=query, top_k=top_k)


async def _run_semantic_search(query: str, top_k: int) -> list[tuple[Document, float]]:
    from app.vectordb import vectordb as vectordb_module

    return await vectordb_module.VECTOR_STORE.asimilarity_search_with_score(query, k=top_k)


async def _run_semantic_search_for_file(
    query: str,
    file_id: str,
    top_k: int,
) -> tuple[list[tuple[Document, float]], str]:
    from app.vectordb import vectordb as vectordb_module

    normalized_file_id = str(file_id or "").strip()
    if not normalized_file_id:
        return [], "missing_file_id"

    normalized_top_k = max(int(top_k or 0), 1)
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
                normalized_filtered: list[tuple[Document, float]] = []
                for item in filtered:
                    if not isinstance(item, tuple) or len(item) < 2:
                        continue
                    doc_candidate = item[0]
                    if not isinstance(doc_candidate, Document):
                        continue
                    metadata = doc_candidate.metadata if isinstance(doc_candidate.metadata, dict) else {}
                    row_file_id, _ = _extract_file_metadata(metadata)
                    if row_file_id != normalized_file_id:
                        continue
                    score = _safe_float(item[1])
                    if score is None:
                        continue
                    normalized_filtered.append((doc_candidate, score))
                    if len(normalized_filtered) >= normalized_top_k:
                        break
                if normalized_filtered:
                    return normalized_filtered, "native_filter"
        except TypeError:
            break
        except Exception:
            continue

    collected: list[tuple[Document, float]] = []
    seen_child_ids: set[str] = set()
    for fallback_k in SEMANTIC_FILE_FILTER_FALLBACK_K_STEPS:
        try:
            items = await _run_semantic_search(query=query, top_k=max(fallback_k, normalized_top_k))
        except Exception:
            continue

        for item in items:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            doc_candidate = item[0]
            if not isinstance(doc_candidate, Document):
                continue

            metadata = doc_candidate.metadata if isinstance(doc_candidate.metadata, dict) else {}
            row_file_id, _ = _extract_file_metadata(metadata)
            if row_file_id != normalized_file_id:
                continue

            try:
                child_chunk_id = _resolve_semantic_child_chunk_id(doc_candidate, query=query)
            except ValueError:
                continue
            if child_chunk_id in seen_child_ids:
                continue
            seen_child_ids.add(child_chunk_id)

            score = _safe_float(item[1])
            if score is None:
                continue
            collected.append((doc_candidate, score))
            if len(collected) >= normalized_top_k:
                return collected[:normalized_top_k], "fallback_post_filter"

    return collected[:normalized_top_k], "fallback_post_filter"


async def _fetch_parent_chunks(parent_ids: list[str]) -> list[dict[str, Any]]:
    from app.vectordb import vectordb as vectordb_module

    rows = await vectordb_module.PARENT_STORE.amget(parent_ids)
    return rows if isinstance(rows, list) else []
