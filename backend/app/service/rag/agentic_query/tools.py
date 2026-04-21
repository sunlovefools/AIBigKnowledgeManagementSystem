"""Scoped retrieval tools used by the agentic query runtime.

All tools in this module are intentionally "thin":
- they validate/sanitize arguments,
- enforce user + collection scope,
- return compact evidence shapes for bounded prompts.
"""

from __future__ import annotations

from typing import Any

from .config_loader import AgenticQueryConfig, read_reference_content
from .models import EvidenceItem

_MIN_TOP_K = 1
_MAX_TOP_K = 20
_MAX_SNIPPET_CHARS = 1400


def _safe_int(raw: Any) -> int | None:
    """Best-effort integer parsing for metadata fields."""

    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _extract_metadata(doc: dict[str, Any]) -> tuple[str, str, int | None, str]:
    """Extract normalized `(file_id, file_name, parent_chunk_number, owner_id)`."""

    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    file_metadata = (
        metadata.get("file_metadata")
        if isinstance(metadata.get("file_metadata"), dict)
        else {}
    )
    parent_chunk_metadata = (
        metadata.get("parent_chunk_metadata")
        if isinstance(metadata.get("parent_chunk_metadata"), dict)
        else {}
    )
    file_id = str(file_metadata.get("file_id") or metadata.get("file_id") or "").strip()
    file_name = str(file_metadata.get("file_name") or metadata.get("source") or "unknown").strip() or "unknown"
    parent_chunk_number = _safe_int(parent_chunk_metadata.get("parent_chunk_number"))
    owner_id = str(metadata.get("user_id") or "").strip()
    return file_id, file_name, parent_chunk_number, owner_id


def _compact_snippet(
    raw_content: str,
    *,
    query: str | None = None,
    max_chars: int = _MAX_SNIPPET_CHARS,
) -> str:
    """Build a bounded snippet, biased around query terms when possible."""

    compact = " ".join(str(raw_content or "").split())
    if len(compact) <= max_chars:
        return compact

    normalized_query = " ".join(str(query or "").split()).lower()
    start_index = -1
    if normalized_query:
        start_index = compact.lower().find(normalized_query)

    if start_index < 0 and normalized_query:
        terms = [
            term
            for term in normalized_query.replace("?", " ").replace(".", " ").split()
            if len(term) >= 4
        ]
        for term in terms:
            start_index = compact.lower().find(term)
            if start_index >= 0:
                break

    if start_index < 0:
        return compact[:max_chars]

    window_start = max(0, start_index - max_chars // 3)
    window_end = min(len(compact), window_start + max_chars)
    snippet = compact[window_start:window_end]
    if window_start > 0:
        snippet = "... " + snippet
    if window_end < len(compact):
        snippet = snippet + " ..."
    return snippet


def _build_evidence_item(doc: dict[str, Any], *, query: str | None = None) -> EvidenceItem | None:
    """Convert a raw vector-store document into a transport-safe evidence item."""

    parent_id = str(doc.get("id") or "").strip()
    if not parent_id:
        return None

    file_id, file_name, parent_chunk_number, _ = _extract_metadata(doc)
    if not file_id:
        return None

    snippet = _compact_snippet(str(doc.get("page_content") or ""), query=query)
    return EvidenceItem(
        parent_id=parent_id,
        file_id=file_id,
        file_name=file_name,
        parent_chunk_number=parent_chunk_number,
        snippet=snippet,
    )


def _is_doc_in_scope(
    doc: dict[str, Any],
    *,
    user_id: str,
    included_file_ids_set: set[str],
) -> bool:
    """Enforce ownership and collection/file scoping for one candidate document."""

    file_id, _, _, owner_id = _extract_metadata(doc)
    if not file_id:
        return False
    if owner_id and owner_id != user_id:
        return False
    if included_file_ids_set and file_id not in included_file_ids_set:
        return False
    return True


async def search_context_tool(
    *,
    query: str,
    top_k: int,
    user_id: str,
    included_file_ids: list[str],
    parent_doc_cache: dict[str, dict[str, Any]],
) -> list[EvidenceItem]:
    """Run scoped retrieval and cache returned parent docs for future turns."""
    from app.vectordb.vectordb import search_and_retrieve_context

    normalized_query = str(query or "").strip()
    if not normalized_query:
        raise ValueError("search_context query must not be empty.")

    bounded_top_k = max(_MIN_TOP_K, min(_MAX_TOP_K, int(top_k)))
    docs = await search_and_retrieve_context(
        query=normalized_query,
        top_k=bounded_top_k,
        user_id=user_id,
        included_file_ids=included_file_ids,
    )
    if not isinstance(docs, list):
        return []

    included_file_ids_set = {
        str(file_id).strip() for file_id in included_file_ids if str(file_id).strip()
    }
    evidence: list[EvidenceItem] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        if not _is_doc_in_scope(
            doc,
            user_id=user_id,
            included_file_ids_set=included_file_ids_set,
        ):
            continue
        item = _build_evidence_item(doc, query=normalized_query)
        if item is None:
            continue
        # Cache by parent_id so follow-up `fetch_parent_chunk` can avoid an extra DB call.
        cached_doc = dict(doc)
        cached_doc["_agentic_query_snippet"] = item.snippet
        parent_doc_cache[item.parent_id] = cached_doc
        evidence.append(item)
    return evidence


async def fetch_parent_chunk_tool(
    *,
    parent_id: str,
    user_id: str,
    included_file_ids: list[str],
    parent_doc_cache: dict[str, dict[str, Any]],
) -> EvidenceItem | None:
    """Fetch one parent chunk from cache first, then backing parent store if needed."""
    from app.vectordb.vectordb import PARENT_STORE

    normalized_parent_id = str(parent_id or "").strip()
    if not normalized_parent_id:
        raise ValueError("fetch_parent_chunk parent_id must not be empty.")

    included_file_ids_set = {
        str(file_id).strip() for file_id in included_file_ids if str(file_id).strip()
    }

    cached_doc = parent_doc_cache.get(normalized_parent_id)
    if isinstance(cached_doc, dict) and _is_doc_in_scope(
        cached_doc,
        user_id=user_id,
        included_file_ids_set=included_file_ids_set,
    ):
        return _build_evidence_item(cached_doc)

    raw_docs = await PARENT_STORE.amget([normalized_parent_id])
    if not isinstance(raw_docs, list) or not raw_docs:
        return None
    raw_doc = raw_docs[0]
    if not isinstance(raw_doc, dict):
        return None

    doc = dict(raw_doc)
    doc["id"] = normalized_parent_id
    if not _is_doc_in_scope(
        doc,
        user_id=user_id,
        included_file_ids_set=included_file_ids_set,
    ):
        return None

    item = _build_evidence_item(doc)
    if item is None:
        return None

    cached_doc = dict(doc)
    cached_doc["_agentic_query_snippet"] = item.snippet
    parent_doc_cache[normalized_parent_id] = cached_doc
    return item


def read_reference_tool(
    *,
    ref_id: str,
    config: AgenticQueryConfig,
    max_chars: int = 2500,
) -> str:
    """Load one optional markdown reference document by `ref_id`."""
    return read_reference_content(config, ref_id, max_chars=max_chars)
