"""Search aggregation and metadata utility helpers for Agent v2."""
from __future__ import annotations

import json
from statistics import mean
from typing import Any

from langchain_core.documents import Document


def _safe_float(value: Any) -> float | None:
    """
    Safely convert a value to float, returning None if conversion fails.
    """
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _average_or_none(values: list[float]) -> float | None:
    """
    Calculate the average of a list of floats, returning None if the list is empty.
    """
    if not values:
        return None
    return round(float(mean(values)), 6)

#TODO: All of these extraction can be more determinstic rather than having a lot of 'or'
def _extract_file_metadata(metadata: dict[str, Any]) -> tuple[str, str]:
    """
    Extract file ID and name from metadata, returning defaults if not found.
    """
    file_metadata = metadata.get("file_metadata")
    if not isinstance(file_metadata, dict):
        file_metadata = {}

    file_id = str(file_metadata.get("file_id") or metadata.get("file_id") or "unknown").strip() or "unknown"
    file_name = str(file_metadata.get("file_name") or metadata.get("file_name") or "unknown").strip() or "unknown"
    return file_id, file_name


def _resolve_lexical_child_chunk_id(row: dict[str, Any], query: str) -> str:
    """
    Extract the child chunk ID from a lexical search hit row, checking multiple possible locations in the metadata.
    """
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    child_metadata = metadata.get("child_chunk_metadata")
    if not isinstance(child_metadata, dict):
        child_metadata = {}

    child_chunk_id = row.get("_id") or metadata.get("child_chunk_id") or child_metadata.get("child_chunk_id")
    child_chunk_id_str = str(child_chunk_id or "").strip()
    if not child_chunk_id_str:
        raise ValueError(f"Missing child_chunk_id in lexical hit for query={query!r}.")
    return child_chunk_id_str


def _resolve_semantic_child_chunk_id(doc: Document, query: str) -> str:
    """
    Extract the child chunk ID from a semantic search hit Document, checking multiple possible locations in the metadata."""
    metadata = doc.metadata if isinstance(doc.metadata, dict) else {}
    child_metadata = metadata.get("child_chunk_metadata")
    if not isinstance(child_metadata, dict):
        child_metadata = {}

    child_chunk_id = (
        getattr(doc, "id", None)
        or metadata.get("child_chunk_id")
        or metadata.get("_id")
        or metadata.get("id")
        or child_metadata.get("child_chunk_id")
    )
    child_chunk_id_str = str(child_chunk_id or "").strip()
    if not child_chunk_id_str:
        raise ValueError(f"Missing child_chunk_id in semantic hit for query={query!r}.")
    return child_chunk_id_str


def _resolve_semantic_parent_id(doc: Document) -> str | None:
    """Extract the parent ID from a semantic search hit Document, checking multiple possible locations in the metadata. Returns None if not found."""
    metadata = doc.metadata if isinstance(doc.metadata, dict) else {}
    child_metadata = metadata.get("child_chunk_metadata")
    if not isinstance(child_metadata, dict):
        child_metadata = {}

    parent_id = child_metadata.get("parent_id") or metadata.get("parent_id")
    parent_id_str = str(parent_id or "").strip()
    return parent_id_str or None


def _extract_parent_chunk_number(metadata: dict[str, Any]) -> int | None:
    """Extract the parent chunk number from metadata, checking multiple possible locations. Returns None if not found or invalid."""
    parent_metadata = metadata.get("parent_chunk_metadata")
    if not isinstance(parent_metadata, dict):
        parent_metadata = {}

    candidate = parent_metadata.get("parent_chunk_number")
    if isinstance(candidate, bool):
        return int(candidate)
    if isinstance(candidate, int):
        return candidate
    if isinstance(candidate, float):
        return int(candidate)
    return None


def _parent_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    """Sort key for parent chunks, prioritizing valid chunk numbers and then parent IDs."""
    chunk_number = item.get("chunk_number")
    normalized = chunk_number if isinstance(chunk_number, int) else 10**9
    return normalized, str(item.get("parent_id", ""))


def _build_parent_chunks_prompt_payload(parent_chunks: list[dict[str, Any]]) -> str:
    """Build a prompt payload string from parent chunk metadata for file filtering."""
    if not parent_chunks:
        return "[none]\nchunk_number: unknown\npage_content: \"\""

    blocks: list[str] = []
    for index, parent_chunk in enumerate(parent_chunks, start=1):
        chunk_number = parent_chunk.get("chunk_number")
        chunk_number_display = str(chunk_number) if isinstance(chunk_number, int) else "unknown"
        page_content = str(parent_chunk.get("page_content") or "")
        block = (
            f"[{index}]\n"
            f"chunk_number: {chunk_number_display}\n"
            f"page_content: {json.dumps(page_content, ensure_ascii=False)}"
        )
        blocks.append(block)
    return "\n\n".join(blocks)


def _normalize_file_filter_result(
    raw_parsed: dict[str, Any],
    *,
    available_chunk_numbers: set[int],
    fallback_reason: str,
) -> dict[str, Any]:
    """
    Normalize the file filter result from the LLM, ensuring it has a valid structure and values.
    """
    decision_raw = str(raw_parsed.get("decision", "")).strip().lower()
    reasoning_summary = str(raw_parsed.get("reasoning_summary", "")).strip()
    if not reasoning_summary:
        reasoning_summary = fallback_reason

    if decision_raw not in {"direct_match", "potential_match", "reject"}:
        decision_raw = "reject"

    confidence = _safe_float(raw_parsed.get("confidence"))
    suggested_numbers_raw = raw_parsed.get("suggested_chunk_numbers")
    if not isinstance(suggested_numbers_raw, list):
        suggested_numbers_raw = []

    normalized_suggestions: list[int] = []
    for item in suggested_numbers_raw:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            chunk_number = item
        elif isinstance(item, float):
            chunk_number = int(item)
        elif isinstance(item, str):
            try:
                chunk_number = int(item.strip())
            except ValueError:
                continue
        else:
            continue
        if chunk_number in available_chunk_numbers and chunk_number not in normalized_suggestions:
            normalized_suggestions.append(chunk_number)

    if decision_raw == "direct_match":
        return {
            "decision": "direct_match",
            "confidence": 1.0,
            "reasoning_summary": reasoning_summary,
            "suggested_chunk_numbers": [],
        }

    if decision_raw == "reject":
        return {
            "decision": "reject",
            "confidence": 0.0,
            "reasoning_summary": reasoning_summary,
            "suggested_chunk_numbers": [],
        }

    if confidence is None:
        confidence = 0.5
    if confidence <= 0.0:
        confidence = 0.01
    if confidence >= 1.0:
        confidence = 0.99

    return {
        "decision": "potential_match",
        "confidence": round(confidence, 6),
        "reasoning_summary": reasoning_summary,
        "suggested_chunk_numbers": normalized_suggestions,
    }


def _extract_fetched_file_ids_from_search_batch(search_batch_result: dict[str, Any]) -> set[str]:
    """
    Extract a set of fetched file IDs from a search batch result, 
    checking for expected structures and handling missing or malformed data gracefully.
    """
    files = search_batch_result.get("files")
    if not isinstance(files, list):
        return set()

    file_ids: set[str] = set()
    for file_item in files:
        if not isinstance(file_item, dict):
            continue
        file_id = str(file_item.get("file_id") or "").strip()
        if file_id:
            file_ids.add(file_id)
    return file_ids

