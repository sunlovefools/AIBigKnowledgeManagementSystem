"""Search aggregation and metadata utility helpers for Agentic Modification."""
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


def _normalize_chunk_number_list(raw_values: Any, *, allowed_chunk_numbers: set[int] | None = None) -> list[int]:
    """Normalize a list-like value into unique chunk numbers preserving order."""
    if not isinstance(raw_values, list):
        return []

    normalized: list[int] = []
    seen: set[int] = set()
    for item in raw_values:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            chunk_number = item
        elif isinstance(item, float):
            chunk_number = int(item)
        elif isinstance(item, str):
            value = item.strip()
            if not value:
                continue
            try:
                chunk_number = int(value)
            except ValueError:
                continue
        else:
            continue
        if allowed_chunk_numbers is not None and chunk_number not in allowed_chunk_numbers:
            continue
        if chunk_number in seen:
            continue
        seen.add(chunk_number)
        normalized.append(chunk_number)
    return normalized


def _extract_parent_child_chunk_ids(metadata: dict[str, Any]) -> list[str]:
    """Extract normalized child chunk IDs linked from a parent chunk metadata payload."""
    parent_metadata = metadata.get("parent_chunk_metadata")
    if not isinstance(parent_metadata, dict):
        parent_metadata = {}

    raw_values = parent_metadata.get("child_chunks_ids")
    if not isinstance(raw_values, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        child_id = str(item or "").strip()
        if not child_id or child_id in seen:
            continue
        seen.add(child_id)
        normalized.append(child_id)
    return normalized


def _parent_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    """Sort key for parent chunks, prioritizing valid chunk numbers and then parent IDs."""
    chunk_number = item.get("chunk_number")
    normalized = chunk_number if isinstance(chunk_number, int) else 10**9
    return normalized, str(item.get("parent_id", ""))


def _build_parent_chunks_prompt_payload(parent_chunks: list[dict[str, Any]]) -> str:
    """Build a prompt payload string from parent chunk metadata for file filtering."""
    if not parent_chunks:
        return "chunk_number: unknown\npage_content: \"\""

    blocks: list[str] = []
    for parent_chunk in parent_chunks:
        chunk_number = parent_chunk.get("chunk_number")
        chunk_number_display = str(chunk_number) if isinstance(chunk_number, int) else "unknown"
        page_content = str(parent_chunk.get("page_content") or "")
        block = (
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
    Normalize Node-4 file filter output into confirmed/potential parent chunk lists.
    """
    reasoning_summary = str(raw_parsed.get("reasoning_summary", "")).strip()
    if not reasoning_summary:
        reasoning_summary = fallback_reason

    confirmed = _normalize_chunk_number_list(
        raw_parsed.get("confirmed_parent_chunks"),
        allowed_chunk_numbers=available_chunk_numbers,
    )
    potential = _normalize_chunk_number_list(
        raw_parsed.get("potential_parent_chunks"),
        allowed_chunk_numbers=available_chunk_numbers,
    )
    confirmed_set = set(confirmed)
    potential = [number for number in potential if number not in confirmed_set]

    return {
        "confirmed_parent_chunks": confirmed,
        "potential_parent_chunks": potential,
        "reasoning_summary": reasoning_summary,
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
