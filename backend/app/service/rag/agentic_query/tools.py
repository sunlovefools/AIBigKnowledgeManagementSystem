"""Scoped retrieval tools used by the agentic query runtime.

All tools in this module are intentionally "thin":
- they validate/sanitize arguments,
- enforce user + collection scope,
- return compact evidence shapes for bounded prompts.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from .config_loader import AgenticQueryConfig, load_skill_content, read_reference_content
from .models import EvidenceItem, FileMatch

_MIN_TOP_K = 1
_MAX_TOP_K = 20
_MAX_SNIPPET_CHARS = 1400
_MAX_FETCH_SNIPPET_CHARS = 4200
_MAX_SKILL_CHARS = 8000
_MAX_FILE_MATCHES = 10
_MAX_FILE_CONTEXT_CHUNKS = 40


def load_skill_tool(
    *,
    skill_name: str,
    config: AgenticQueryConfig,
    loaded_skill_cache: dict[str, dict[str, Any]],
    max_chars: int = _MAX_SKILL_CHARS,
) -> dict[str, Any]:
    """Load one full skill body lazily, caching it for the current run."""

    normalized_skill_name = str(skill_name or "").strip().lower().replace("_", "-")
    if not normalized_skill_name:
        raise ValueError("load_skill skill_name must not be empty.")

    cached = loaded_skill_cache.get(normalized_skill_name)
    if isinstance(cached, dict):
        return {**cached, "cached": True}

    payload = load_skill_content(
        config,
        normalized_skill_name,
        max_chars=max_chars,
    )
    loaded_skill_cache[normalized_skill_name] = dict(payload)
    return {**payload, "cached": False}


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


def _extract_table_semantic_metadata(doc: dict[str, Any]) -> dict[str, Any]:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    parent_chunk_metadata = (
        metadata.get("parent_chunk_metadata")
        if isinstance(metadata.get("parent_chunk_metadata"), dict)
        else {}
    )
    table_semantic = parent_chunk_metadata.get("table_semantic")
    return table_semantic if isinstance(table_semantic, dict) else {}


def _format_structured_table_view(doc: dict[str, Any], *, max_chars: int = 2600) -> str:
    """Render table metadata as a compact row/field view for answer synthesis."""

    table_semantic = _extract_table_semantic_metadata(doc)
    if not table_semantic:
        return ""

    parts: list[str] = []
    section = str(table_semantic.get("section_name") or "").strip()
    parent_section = str(table_semantic.get("parent_section_name") or "").strip()
    subsection = str(table_semantic.get("subsection_name") or "").strip()
    description = str(table_semantic.get("general_description") or "").strip()

    title_bits = []
    if parent_section and subsection:
        title_bits.append(f"Parent section: {parent_section}")
        title_bits.append(f"Subsection: {subsection}")
    elif section:
        title_bits.append(f"Section: {section}")
    if title_bits:
        parts.append("; ".join(title_bits))
    if description:
        parts.append(f"Table description: {description}")

    criteria = table_semantic.get("criteria_names")
    if isinstance(criteria, list):
        normalized = [str(item).strip() for item in criteria if str(item).strip()]
        if normalized:
            parts.append("Item labels: " + "; ".join(normalized[:12]))

    weights = table_semantic.get("weights")
    if isinstance(weights, list):
        normalized = [str(item).strip() for item in weights if str(item).strip()]
        if normalized:
            parts.append("Weights/key numeric values: " + ", ".join(normalized[:12]))

    rows = table_semantic.get("structured_rows")
    if isinstance(rows, list) and rows:
        row_lines: list[str] = []
        for raw_row in rows[:10]:
            if not isinstance(raw_row, dict):
                continue
            label = str(raw_row.get("label") or "").strip()
            row_weights = raw_row.get("weights")
            weight_text = ""
            if isinstance(row_weights, list):
                normalized_weights = [
                    str(item).strip() for item in row_weights if str(item).strip()
                ]
                if normalized_weights:
                    weight_text = " | weights: " + ", ".join(normalized_weights[:4])
            cells = raw_row.get("cells")
            cell_text = ""
            if isinstance(cells, dict):
                cell_parts = [
                    f"{str(key).strip()}: {str(value).strip()}"
                    for key, value in list(cells.items())[:6]
                    if str(key).strip() and str(value).strip()
                ]
                if cell_parts:
                    cell_text = " | " + "; ".join(cell_parts)
            row_text = str(raw_row.get("text") or "").strip()
            if not cell_text and row_text:
                cell_text = " | " + row_text
            if label:
                row_lines.append(f"- {label}{weight_text}{cell_text}")
            elif cell_text:
                row_lines.append(f"-{weight_text}{cell_text}")
        if row_lines:
            parts.append("Rows:\n" + "\n".join(row_lines))

    rendered = "\n".join(parts).strip()
    if not rendered:
        return ""
    return _compact_snippet(rendered, max_chars=max_chars)


def _included_file_ids_set(included_file_ids: list[str] | None) -> set[str] | None:
    """Normalize optional file scope into a set."""

    if included_file_ids is None:
        return None
    return {
        str(file_id).strip()
        for file_id in included_file_ids
        if str(file_id).strip()
    }


def _normalize_parent_store_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a raw parent-store row into the runtime document shape."""

    if not isinstance(row, dict):
        return None
    parent_id = str(row.get("_id") or row.get("id") or "").strip()
    raw_doc = row.get("value") if isinstance(row.get("value"), dict) else row
    if not isinstance(raw_doc, dict):
        return None
    doc = dict(raw_doc)
    if parent_id:
        doc["id"] = parent_id
    if not str(doc.get("id") or "").strip():
        return None
    if not isinstance(doc.get("metadata"), dict):
        doc["metadata"] = {}
    doc["page_content"] = str(doc.get("page_content") or "")
    doc["type"] = str(doc.get("type") or "Document")
    return doc


def _file_name_matches_query(file_name: str, query: str) -> bool:
    """Return true when all meaningful query terms occur in the filename."""

    normalized_name = str(file_name or "").lower()
    terms = [
        term
        for term in (
            str(query or "")
            .lower()
            .replace(".", " ")
            .replace("_", " ")
            .replace("-", " ")
            .split()
        )
        if len(term) >= 2 and term not in {"file", "pdf", "doc", "docx"}
    ]
    if not terms:
        return bool(normalized_name.strip())
    return all(term in normalized_name for term in terms) or any(
        len(term) >= 4 and term in normalized_name for term in terms
    )


def _query_anchors(query: str | None) -> list[str]:
    """Extract useful search anchors from mixed-language user queries."""

    normalized_query = " ".join(str(query or "").split()).lower()
    if not normalized_query:
        return []

    anchors: list[str] = []
    english_tokens = re.findall(r"[a-z][a-z0-9_-]*", normalized_query)
    stopwords = {
        "file",
        "pdf",
        "doc",
        "docx",
        "table",
        "rubric",
        "criteria",
        "criterion",
        "standard",
        "standards",
    }

    for index in range(len(english_tokens) - 1):
        phrase = f"{english_tokens[index]} {english_tokens[index + 1]}"
        if phrase not in anchors:
            anchors.append(phrase)

    for token in english_tokens:
        if len(token) >= 3 and token not in stopwords and token not in anchors:
            anchors.append(token)

    split_terms = [
        term
        for term in normalized_query.replace("?", " ").replace(".", " ").split()
        if len(term) >= 4
    ]
    for term in split_terms:
        if term not in anchors:
            anchors.append(term)

    return anchors


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

    start_index = -1
    compact_lower = compact.lower()
    for anchor in _query_anchors(query):
        start_index = compact_lower.find(anchor)
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


def _build_evidence_item(
    doc: dict[str, Any],
    *,
    query: str | None = None,
    max_chars: int = _MAX_SNIPPET_CHARS,
) -> EvidenceItem | None:
    """Convert a raw vector-store document into a transport-safe evidence item."""

    parent_id = str(doc.get("id") or "").strip()
    if not parent_id:
        return None

    file_id, file_name, parent_chunk_number, _ = _extract_metadata(doc)
    if not file_id:
        return None

    snippet = _compact_snippet(str(doc.get("page_content") or ""), query=query, max_chars=max_chars)
    structured_view = _format_structured_table_view(doc)
    return EvidenceItem(
        parent_id=parent_id,
        file_id=file_id,
        file_name=file_name,
        parent_chunk_number=parent_chunk_number,
        snippet=snippet,
        structured_view=structured_view,
    )


def _is_doc_in_scope(
    doc: dict[str, Any],
    *,
    user_id: str,
    included_file_ids_set: set[str] | None,
) -> bool:
    """Enforce ownership and collection/file scoping for one candidate document."""

    file_id, _, _, owner_id = _extract_metadata(doc)
    if not file_id:
        return False
    if owner_id and owner_id != user_id:
        return False
    if included_file_ids_set is not None and file_id not in included_file_ids_set:
        return False
    return True


async def _find_file_matches(
    *,
    query: str,
    user_id: str,
    included_file_ids: list[str] | None,
    limit: int,
) -> list[FileMatch]:
    """Search parent-store file headers by filename within user/collection scope."""

    from app.vectordb.vectordb import PARENT_STORE

    normalized_query = str(query or "").strip()
    normalized_user_id = str(user_id or "").strip()
    if not normalized_query:
        raise ValueError("search_files query must not be empty.")
    if not normalized_user_id:
        raise ValueError("user_id must be a non-empty string.")

    included_file_ids_set = _included_file_ids_set(included_file_ids)
    bounded_limit = max(1, min(_MAX_FILE_MATCHES, int(limit)))

    def _query_rows() -> list[dict[str, Any]]:
        collection = PARENT_STORE.collection
        filter_doc: dict[str, Any] = {
            "value.metadata.user_id": normalized_user_id,
            "value.metadata.parent_chunk_metadata.parent_chunk_number": 0,
        }
        if included_file_ids_set:
            filter_doc["value.metadata.file_metadata.file_id"] = {"$in": sorted(included_file_ids_set)}
        projection_doc = {"_id": True, "value": True}
        rows: list[dict[str, Any]] = []
        for row in collection.find(filter_doc, projection=projection_doc):
            if isinstance(row, dict):
                rows.append(row)
        return rows

    rows = await asyncio.to_thread(_query_rows)
    matches_by_file_id: dict[str, FileMatch] = {}
    for row in rows:
        doc = _normalize_parent_store_row(row)
        if doc is None or not _is_doc_in_scope(
            doc,
            user_id=normalized_user_id,
            included_file_ids_set=included_file_ids_set,
        ):
            continue
        file_id, file_name, parent_chunk_number, _ = _extract_metadata(doc)
        if not file_id or file_id in matches_by_file_id:
            continue
        if not _file_name_matches_query(file_name, normalized_query):
            continue
        if parent_chunk_number not in (None, 0):
            continue
        matches_by_file_id[file_id] = FileMatch(
            file_id=file_id,
            file_name=file_name,
            first_parent_id=str(doc.get("id") or "").strip() or None,
            preview=_compact_snippet(str(doc.get("page_content") or ""), max_chars=320),
        )
        if len(matches_by_file_id) >= bounded_limit:
            break

    return list(matches_by_file_id.values())


async def search_files_tool(
    *,
    query: str,
    limit: int,
    user_id: str,
    included_file_ids: list[str] | None,
) -> list[FileMatch]:
    """Find scoped files by filename, returning file IDs for file-level reads."""

    return await _find_file_matches(
        query=query,
        user_id=user_id,
        included_file_ids=included_file_ids,
        limit=limit,
    )


async def search_context_tool(
    *,
    query: str,
    top_k: int,
    user_id: str,
    included_file_ids: list[str] | None,
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

    included_file_ids_set = (
        None
        if included_file_ids is None
        else {
            str(file_id).strip()
            for file_id in included_file_ids
            if str(file_id).strip()
        }
    )
    seen_parent_ids = {
        str(parent_id).strip()
        for parent_id in parent_doc_cache.keys()
        if str(parent_id).strip()
    }
    fresh_candidates: list[tuple[EvidenceItem, dict[str, Any]]] = []
    fallback_candidates: list[tuple[EvidenceItem, dict[str, Any]]] = []

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
        cached_doc = dict(doc)
        cached_doc["_agentic_query_snippet"] = item.snippet
        cached_doc["_agentic_query_structured_view"] = item.structured_view
        if item.parent_id in seen_parent_ids:
            fallback_candidates.append((item, cached_doc))
        else:
            fresh_candidates.append((item, cached_doc))

    selected_candidates = fresh_candidates if fresh_candidates else fallback_candidates
    evidence: list[EvidenceItem] = []
    for item, cached_doc in selected_candidates:
        # Cache by parent_id so follow-up `fetch_parent_chunk` can avoid an extra DB call.
        parent_doc_cache[item.parent_id] = cached_doc
        evidence.append(item)
    return evidence


async def fetch_parent_chunk_tool(
    *,
    parent_id: str,
    user_id: str,
    included_file_ids: list[str] | None,
    parent_doc_cache: dict[str, dict[str, Any]],
    query: str | None = None,
) -> EvidenceItem | None:
    """Fetch one parent chunk from cache first, then backing parent store if needed."""
    from app.vectordb.vectordb import PARENT_STORE

    normalized_parent_id = str(parent_id or "").strip()
    if not normalized_parent_id:
        raise ValueError("fetch_parent_chunk parent_id must not be empty.")

    included_file_ids_set = (
        None
        if included_file_ids is None
        else {
            str(file_id).strip()
            for file_id in included_file_ids
            if str(file_id).strip()
        }
    )

    cached_doc = parent_doc_cache.get(normalized_parent_id)
    if isinstance(cached_doc, dict) and _is_doc_in_scope(
        cached_doc,
        user_id=user_id,
        included_file_ids_set=included_file_ids_set,
    ):
        return _build_evidence_item(cached_doc, query=query, max_chars=_MAX_FETCH_SNIPPET_CHARS)

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

    item = _build_evidence_item(doc, query=query, max_chars=_MAX_FETCH_SNIPPET_CHARS)
    if item is None:
        return None

    cached_doc = dict(doc)
    cached_doc["_agentic_query_snippet"] = item.snippet
    cached_doc["_agentic_query_structured_view"] = item.structured_view
    parent_doc_cache[normalized_parent_id] = cached_doc
    return item


async def fetch_file_context_tool(
    *,
    file_id: str | None,
    file_name: str | None,
    max_chunks: int,
    user_id: str,
    included_file_ids: list[str] | None,
    parent_doc_cache: dict[str, dict[str, Any]],
    query: str | None = None,
) -> list[EvidenceItem]:
    """Fetch ordered parent chunks for one scoped file and cache them."""

    from app.vectordb.vectordb import PARENT_STORE

    normalized_file_id = str(file_id or "").strip()
    normalized_file_name = str(file_name or "").strip()
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        raise ValueError("user_id must be a non-empty string.")
    if not normalized_file_id and not normalized_file_name:
        raise ValueError("fetch_file_context requires file_id or file_name.")

    included_file_ids_set = _included_file_ids_set(included_file_ids)
    if (
        included_file_ids_set is not None
        and normalized_file_id
        and normalized_file_id not in included_file_ids_set
    ):
        return []

    if not normalized_file_id and normalized_file_name:
        matches = await _find_file_matches(
            query=normalized_file_name,
            user_id=normalized_user_id,
            included_file_ids=included_file_ids,
            limit=1,
        )
        if not matches:
            return []
        normalized_file_id = matches[0].file_id

    bounded_max_chunks = max(1, min(_MAX_FILE_CONTEXT_CHUNKS, int(max_chunks)))

    def _query_rows() -> list[dict[str, Any]]:
        collection = PARENT_STORE.collection
        filter_doc: dict[str, Any] = {
            "value.metadata.user_id": normalized_user_id,
            "value.metadata.file_metadata.file_id": normalized_file_id,
        }
        projection_doc = {"_id": True, "value": True}
        rows: list[dict[str, Any]] = []
        for row in collection.find(filter_doc, projection=projection_doc):
            if isinstance(row, dict):
                rows.append(row)
        return rows

    rows = await asyncio.to_thread(_query_rows)
    docs: list[dict[str, Any]] = []
    for row in rows:
        doc = _normalize_parent_store_row(row)
        if doc is None:
            continue
        if not _is_doc_in_scope(
            doc,
            user_id=normalized_user_id,
            included_file_ids_set=included_file_ids_set,
        ):
            continue
        docs.append(doc)

    docs.sort(
        key=lambda doc: (
            _extract_metadata(doc)[2] is None,
            _extract_metadata(doc)[2] or 0,
            str(doc.get("id") or ""),
        )
    )

    evidence: list[EvidenceItem] = []
    for doc in docs[:bounded_max_chunks]:
        item = _build_evidence_item(doc, query=query, max_chars=_MAX_FETCH_SNIPPET_CHARS)
        if item is None:
            continue
        cached_doc = dict(doc)
        cached_doc["_agentic_query_snippet"] = item.snippet
        cached_doc["_agentic_query_structured_view"] = item.structured_view
        parent_doc_cache[item.parent_id] = cached_doc
        evidence.append(item)

    return evidence


def read_reference_tool(
    *,
    skill_name: str,
    ref_id: str,
    config: AgenticQueryConfig,
    max_chars: int = 2500,
) -> str:
    """Load one optional markdown reference document by skill + `ref_id`."""
    return read_reference_content(config, skill_name, ref_id, max_chars=max_chars)
