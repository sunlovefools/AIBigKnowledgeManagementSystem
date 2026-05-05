"""Tool implementations for the Skills-style modification runtime."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from . import llm_client
from .config_loader import (
    AgenticModificationSkillConfig,
    load_skill_content,
    read_reference_content,
)
from .models import (
    ChunkWindow,
    EvidenceItem,
    FileMatch,
    FileOutlineChunk,
    FileWorkerResult,
    ParentChunk,
    ProposalItem,
)

_MAX_TOP_K = 20
_MIN_TOP_K = 1
_MAX_FILE_MATCHES = 10
_MAX_OUTLINE_CHUNKS = 120
_MAX_WINDOW_RADIUS = 3
_MAX_SNIPPET_CHARS = 1200
_MAX_WORKER_CONCURRENCY = 3
_QUERY_STOPWORDS = {
    "add",
    "all",
    "and",
    "any",
    "for",
    "from",
    "into",
    "the",
    "this",
    "that",
    "then",
    "to",
    "with",
}


def load_skill_tool(
    *,
    skill_name: str,
    config: AgenticModificationSkillConfig,
    max_chars: int = 8000,
) -> dict[str, Any]:
    return load_skill_content(config, skill_name, max_chars=max_chars)


def read_reference_tool(
    *,
    skill_name: str,
    ref_id: str,
    config: AgenticModificationSkillConfig,
    max_chars: int = 3000,
) -> str:
    return read_reference_content(config, skill_name, ref_id, max_chars=max_chars)


def _safe_int(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _compact_snippet(text: str, *, max_chars: int = _MAX_SNIPPET_CHARS) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(0, max_chars - 3)].rstrip() + "..."


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", str(query or "").casefold()):
        token = raw.strip("-_")
        if len(token) < 3 or token in _QUERY_STOPWORDS or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return terms


def _query_match_score(haystack: str, query: str) -> int:
    normalized_haystack = str(haystack or "").casefold()
    normalized_query = str(query or "").strip().casefold()
    if not normalized_query:
        return 1
    if normalized_query in normalized_haystack:
        return 1000
    score = 0
    for term in _query_terms(query):
        if term in normalized_haystack:
            score += 1
    return score


def _extract_metadata(doc: dict[str, Any]) -> tuple[str, str, int | None, str]:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    file_metadata = metadata.get("file_metadata") if isinstance(metadata.get("file_metadata"), dict) else {}
    parent_metadata = (
        metadata.get("parent_chunk_metadata")
        if isinstance(metadata.get("parent_chunk_metadata"), dict)
        else {}
    )
    file_id = str(file_metadata.get("file_id") or metadata.get("file_id") or "").strip()
    file_name = str(file_metadata.get("file_name") or metadata.get("file_name") or "").strip() or "unknown"
    chunk_number = _safe_int(
        _first_present(
            parent_metadata.get("parent_chunk_number"),
            parent_metadata.get("chunk_number"),
            metadata.get("parent_chunk_number"),
            metadata.get("chunk_number"),
        )
    )
    user_id = str(metadata.get("user_id") or "").strip()
    return file_id, file_name, chunk_number, user_id


def _included_file_ids_set(included_file_ids: list[str] | None) -> set[str] | None:
    if included_file_ids is None:
        return None
    return {str(file_id).strip() for file_id in included_file_ids if str(file_id).strip()}


def _is_doc_in_scope(
    doc: dict[str, Any],
    *,
    user_id: str,
    included_file_ids_set: set[str] | None,
) -> bool:
    file_id, _file_name, _chunk_number, doc_user_id = _extract_metadata(doc)
    if str(user_id or "").strip() and doc_user_id != str(user_id or "").strip():
        return False
    if included_file_ids_set is not None and file_id not in included_file_ids_set:
        return False
    return bool(file_id)


def _normalize_parent_store_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    raw_doc = row.get("value") if isinstance(row.get("value"), dict) else None
    if not isinstance(raw_doc, dict):
        return None
    doc = dict(raw_doc)
    doc["id"] = str(row.get("_id") or raw_doc.get("id") or raw_doc.get("parent_chunk_id") or "").strip()
    return doc


def _build_evidence_item(doc: dict[str, Any], *, query: str | None = None) -> EvidenceItem | None:
    parent_id = str(doc.get("id") or doc.get("parent_chunk_id") or "").strip()
    file_id, file_name, chunk_number, _user_id = _extract_metadata(doc)
    if not parent_id or not file_id:
        return None
    content = str(doc.get("page_content") or "")
    snippet = str(doc.get("_agentic_modification_skill_snippet") or "").strip()
    if not snippet:
        snippet = _compact_snippet(content)
    if query and query.casefold() in content.casefold():
        index = content.casefold().find(query.casefold())
        start = max(0, index - 320)
        snippet = _compact_snippet(content[start : start + 900])
    return EvidenceItem(
        parent_id=parent_id,
        file_id=file_id,
        file_name=file_name,
        parent_chunk_number=chunk_number,
        snippet=snippet,
    )


def _build_parent_chunk(doc: dict[str, Any]) -> ParentChunk | None:
    parent_id = str(doc.get("id") or doc.get("parent_chunk_id") or "").strip()
    file_id, file_name, chunk_number, _user_id = _extract_metadata(doc)
    if not parent_id or not file_id:
        return None
    return ParentChunk(
        parent_id=parent_id,
        file_id=file_id,
        file_name=file_name,
        parent_chunk_number=chunk_number,
        content=str(doc.get("page_content") or ""),
    )


def _heading_from_content(content: str) -> str | None:
    for line in str(content or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped[:160]
    return None


async def _find_file_matches(
    *,
    query: str,
    user_id: str,
    included_file_ids: list[str] | None,
    limit: int,
) -> list[FileMatch]:
    from app.vectordb.vectordb import PARENT_STORE

    included_set = _included_file_ids_set(included_file_ids)
    bounded_limit = max(1, min(_MAX_FILE_MATCHES, int(limit)))

    def _query_rows() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        filter_doc: dict[str, Any] = {"value.metadata.user_id": str(user_id or "").strip()}
        if included_set is not None:
            filter_doc["value.metadata.file_metadata.file_id"] = {"$in": sorted(included_set)}
        for row in PARENT_STORE.collection.find(filter_doc, projection={"_id": True, "value": True}):
            if isinstance(row, dict):
                rows.append(row)
        return rows

    rows = await asyncio.to_thread(_query_rows)
    scored_matches: dict[str, tuple[int, FileMatch]] = {}
    terms = _query_terms(query)
    min_score = 2 if len(terms) >= 3 else 1
    for row in rows:
        doc = _normalize_parent_store_row(row)
        if doc is None:
            continue
        if not _is_doc_in_scope(doc, user_id=user_id, included_file_ids_set=included_set):
            continue
        file_id, file_name, _chunk_number, _doc_user_id = _extract_metadata(doc)
        haystack = f"{file_name}\n{doc.get('page_content') or ''}"
        score = _query_match_score(haystack, query)
        if score < min_score:
            continue
        existing = scored_matches.get(file_id)
        if existing is not None and existing[0] >= score:
            continue
        scored_matches[file_id] = (
            score,
            FileMatch(
                file_id=file_id,
                file_name=file_name,
                first_parent_id=str(doc.get("id") or "").strip() or None,
                preview=_compact_snippet(str(doc.get("page_content") or ""), max_chars=320),
            ),
        )

    return [
        match
        for _score, match in sorted(
            scored_matches.values(),
            key=lambda item: (-item[0], item[1].file_name.casefold(), item[1].file_id),
        )[:bounded_limit]
    ]


async def search_files_tool(
    *,
    query: str,
    limit: int,
    user_id: str,
    included_file_ids: list[str] | None,
) -> list[FileMatch]:
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
    from app.vectordb.vectordb import search_and_retrieve_context

    normalized_query = str(query or "").strip()
    if not normalized_query:
        raise ValueError("search_context query must not be empty.")
    docs = await search_and_retrieve_context(
        query=normalized_query,
        top_k=max(_MIN_TOP_K, min(_MAX_TOP_K, int(top_k))),
        user_id=user_id,
        included_file_ids=included_file_ids,
    )
    included_set = _included_file_ids_set(included_file_ids)
    evidence: list[EvidenceItem] = []
    if not isinstance(docs, list):
        return evidence
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        if not _is_doc_in_scope(doc, user_id=user_id, included_file_ids_set=included_set):
            continue
        item = _build_evidence_item(doc, query=normalized_query)
        if item is None:
            continue
        cached_doc = dict(doc)
        cached_doc["_agentic_modification_skill_snippet"] = item.snippet
        parent_doc_cache[item.parent_id] = cached_doc
        evidence.append(item)
    return evidence


async def _fetch_file_docs(
    *,
    file_id: str | None,
    file_name: str | None,
    max_chunks: int,
    user_id: str,
    included_file_ids: list[str] | None,
    parent_doc_cache: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    from app.vectordb.vectordb import PARENT_STORE

    normalized_file_id = str(file_id or "").strip()
    normalized_file_name = str(file_name or "").strip()
    if not normalized_file_id and normalized_file_name:
        matches = await _find_file_matches(
            query=normalized_file_name,
            user_id=user_id,
            included_file_ids=included_file_ids,
            limit=1,
        )
        if matches:
            normalized_file_id = matches[0].file_id
    if not normalized_file_id:
        raise ValueError("file_id or file_name is required.")

    included_set = _included_file_ids_set(included_file_ids)
    if included_set is not None and normalized_file_id not in included_set:
        return []

    def _query_rows() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        filter_doc = {
            "value.metadata.user_id": str(user_id or "").strip(),
            "value.metadata.file_metadata.file_id": normalized_file_id,
        }
        for row in PARENT_STORE.collection.find(filter_doc, projection={"_id": True, "value": True}):
            if isinstance(row, dict):
                rows.append(row)
        return rows

    rows = await asyncio.to_thread(_query_rows)
    docs: list[dict[str, Any]] = []
    for row in rows:
        doc = _normalize_parent_store_row(row)
        if doc is None:
            continue
        if not _is_doc_in_scope(doc, user_id=user_id, included_file_ids_set=included_set):
            continue
        docs.append(doc)
    docs.sort(key=lambda doc: (_extract_metadata(doc)[2] is None, _extract_metadata(doc)[2] or 0, str(doc.get("id") or "")))
    bounded_docs = docs[: max(1, min(_MAX_OUTLINE_CHUNKS, int(max_chunks)))]
    for doc in bounded_docs:
        parent_id = str(doc.get("id") or "").strip()
        if parent_id:
            parent_doc_cache[parent_id] = dict(doc)
    return bounded_docs


async def fetch_file_outline_tool(
    *,
    file_id: str | None,
    file_name: str | None,
    max_chunks: int,
    user_id: str,
    included_file_ids: list[str] | None,
    parent_doc_cache: dict[str, dict[str, Any]],
) -> list[FileOutlineChunk]:
    docs = await _fetch_file_docs(
        file_id=file_id,
        file_name=file_name,
        max_chunks=max_chunks,
        user_id=user_id,
        included_file_ids=included_file_ids,
        parent_doc_cache=parent_doc_cache,
    )
    outline: list[FileOutlineChunk] = []
    for doc in docs:
        parent_id = str(doc.get("id") or "").strip()
        file_id_value, file_name_value, chunk_number, _user_id = _extract_metadata(doc)
        content = str(doc.get("page_content") or "")
        if not parent_id or not file_id_value:
            continue
        outline.append(
            FileOutlineChunk(
                parent_id=parent_id,
                file_id=file_id_value,
                file_name=file_name_value,
                parent_chunk_number=chunk_number,
                heading=_heading_from_content(content),
                preview=_compact_snippet(content, max_chars=420),
                size=len(content),
            )
        )
    return outline


async def fetch_parent_chunk_tool(
    *,
    parent_id: str,
    user_id: str,
    included_file_ids: list[str] | None,
    parent_doc_cache: dict[str, dict[str, Any]],
) -> ParentChunk | None:
    from app.vectordb.vectordb import PARENT_STORE

    normalized_parent_id = str(parent_id or "").strip()
    if not normalized_parent_id:
        raise ValueError("parent_id must not be empty.")
    included_set = _included_file_ids_set(included_file_ids)
    cached_doc = parent_doc_cache.get(normalized_parent_id)
    if isinstance(cached_doc, dict) and _is_doc_in_scope(
        cached_doc,
        user_id=user_id,
        included_file_ids_set=included_set,
    ):
        return _build_parent_chunk(cached_doc)

    raw_docs = await PARENT_STORE.amget([normalized_parent_id])
    if not isinstance(raw_docs, list) or not raw_docs or not isinstance(raw_docs[0], dict):
        return None
    doc = dict(raw_docs[0])
    doc["id"] = normalized_parent_id
    if not _is_doc_in_scope(doc, user_id=user_id, included_file_ids_set=included_set):
        return None
    parent_doc_cache[normalized_parent_id] = dict(doc)
    return _build_parent_chunk(doc)


async def fetch_chunk_window_tool(
    *,
    file_id: str,
    center_parent_id: str | None,
    center_chunk_number: int | None,
    before: int,
    after: int,
    user_id: str,
    included_file_ids: list[str] | None,
    parent_doc_cache: dict[str, dict[str, Any]],
) -> ChunkWindow:
    outline_docs = await _fetch_file_docs(
        file_id=file_id,
        file_name=None,
        max_chunks=_MAX_OUTLINE_CHUNKS,
        user_id=user_id,
        included_file_ids=included_file_ids,
        parent_doc_cache=parent_doc_cache,
    )
    center_index: int | None = None
    normalized_center_parent_id = str(center_parent_id or "").strip()
    for index, doc in enumerate(outline_docs):
        parent_id = str(doc.get("id") or "").strip()
        chunk_number = _extract_metadata(doc)[2]
        if normalized_center_parent_id and parent_id == normalized_center_parent_id:
            center_index = index
            break
        if center_chunk_number is not None and chunk_number == center_chunk_number:
            center_index = index
            break
    if center_index is None:
        return ChunkWindow(file_id=str(file_id), file_name="unknown", chunks=[])

    bounded_before = max(0, min(_MAX_WINDOW_RADIUS, int(before)))
    bounded_after = max(0, min(_MAX_WINDOW_RADIUS, int(after)))
    selected = outline_docs[max(0, center_index - bounded_before) : center_index + bounded_after + 1]
    chunks = [chunk for doc in selected if (chunk := _build_parent_chunk(doc)) is not None]
    file_name = chunks[0].file_name if chunks else "unknown"
    return ChunkWindow(file_id=str(file_id), file_name=file_name, chunks=chunks)


def _safe_json_object(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start == -1:
            raise
        payload, _end_index = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object.")
    return payload


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _fallback_targets_from_outline(
    *,
    outline: list[FileOutlineChunk],
    instruction: str,
) -> list[dict[str, str]]:
    terms = _query_terms(instruction)
    target_terms = [term for term in terms if term not in {"penny", "meeting", "minutes"}]
    if not target_terms:
        target_terms = terms

    targets: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in outline:
        haystack = f"{item.heading or ''}\n{item.preview}".casefold()
        if not any(term in haystack for term in target_terms):
            continue
        if item.parent_id in seen:
            continue
        seen.add(item.parent_id)
        targets.append(
            {
                "parent_id": item.parent_id,
                "reason": "Fallback selected this chunk because it matched the edit instruction terms.",
            }
        )
    return targets


async def _run_file_worker(
    *,
    file_id: str,
    instruction: str,
    user_id: str,
    included_file_ids: list[str] | None,
    parent_doc_cache: dict[str, dict[str, Any]],
    session: Any,
    timeout_s: float,
) -> tuple[FileWorkerResult, dict[str, int], int]:
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    llm_calls = 0
    outline = await fetch_file_outline_tool(
        file_id=file_id,
        file_name=None,
        max_chunks=80,
        user_id=user_id,
        included_file_ids=included_file_ids,
        parent_doc_cache=parent_doc_cache,
    )
    file_name = outline[0].file_name if outline else "unknown"
    if not outline:
        return FileWorkerResult(file_id=file_id, file_name=file_name, skipped=True, reason="No scoped chunks found."), usage_total, llm_calls

    selector_messages = [
        {
            "role": "system",
            "content": (
                "You are a file-scoped document modification worker. Select every parent chunk "
                "that may need editing. Return only JSON with keys targets and reason. "
                "targets must be an array of {parent_id, reason}."
            ),
        },
        {
            "role": "user",
            "content": _json_dumps(
                {
                    "instruction": instruction,
                    "file_id": file_id,
                    "file_name": file_name,
                    "outline": [item.model_dump() for item in outline],
                }
            ),
        },
    ]
    selected_text, usage = await llm_client.call_action_model(
        messages=selector_messages,
        session=session,
        max_tokens=1200,
        timeout_s=timeout_s,
    )
    llm_calls += 1
    for key in usage_total:
        usage_total[key] += int(usage.get(key, 0) or 0)
    selected_payload = _safe_json_object(selected_text)
    raw_targets = selected_payload.get("targets")
    targets: list[dict[str, str]] = []
    if isinstance(raw_targets, list):
        seen: set[str] = set()
        outline_parent_ids = {item.parent_id for item in outline}
        for raw in raw_targets:
            if not isinstance(raw, dict):
                continue
            parent_id = str(raw.get("parent_id") or raw.get("parentId") or "").strip()
            if not parent_id or parent_id in seen or parent_id not in outline_parent_ids:
                continue
            seen.add(parent_id)
            targets.append({"parent_id": parent_id, "reason": str(raw.get("reason") or "").strip()})
    if not targets:
        targets = _fallback_targets_from_outline(outline=outline, instruction=instruction)
    if not targets:
        return (
            FileWorkerResult(
                file_id=file_id,
                file_name=file_name,
                skipped=True,
                reason=str(selected_payload.get("reason") or "No target chunks selected.").strip(),
            ),
            usage_total,
            llm_calls,
        )

    windows: list[dict[str, Any]] = []
    exact_chunks_by_parent_id: dict[str, ParentChunk] = {}
    for target in targets[:20]:
        parent_id = target["parent_id"]
        chunk = await fetch_parent_chunk_tool(
            parent_id=parent_id,
            user_id=user_id,
            included_file_ids=included_file_ids,
            parent_doc_cache=parent_doc_cache,
        )
        if chunk is None:
            continue
        exact_chunks_by_parent_id[parent_id] = chunk
        window = await fetch_chunk_window_tool(
            file_id=file_id,
            center_parent_id=parent_id,
            center_chunk_number=chunk.parent_chunk_number,
            before=1,
            after=1,
            user_id=user_id,
            included_file_ids=included_file_ids,
            parent_doc_cache=parent_doc_cache,
        )
        windows.append(
            {
                "target_parent_id": parent_id,
                "reason": target.get("reason") or "",
                "window": [item.model_dump() for item in window.chunks],
            }
        )

    if not exact_chunks_by_parent_id:
        return (
            FileWorkerResult(
                file_id=file_id,
                file_name=file_name,
                explored_parent_ids=[target["parent_id"] for target in targets],
                skipped=True,
                reason="Selected target chunks could not be loaded.",
            ),
            usage_total,
            llm_calls,
        )

    editor_messages = [
        {
            "role": "system",
            "content": (
                "You generate reviewable parent-chunk edit proposals. Return only JSON with key proposals. "
                "Each proposal must be {parent_id, proposed}. Preserve unrelated content and apply all edits "
                "inside the same parent chunk in one proposed full replacement. If a target should not change, omit it."
            ),
        },
        {
            "role": "user",
            "content": _json_dumps(
                {
                    "instruction": instruction,
                    "file_id": file_id,
                    "file_name": file_name,
                    "target_chunks": [chunk.model_dump() for chunk in exact_chunks_by_parent_id.values()],
                    "context_windows": windows,
                }
            ),
        },
    ]
    edited_text, usage = await llm_client.call_action_model(
        messages=editor_messages,
        session=session,
        max_tokens=4000,
        timeout_s=timeout_s,
    )
    llm_calls += 1
    for key in usage_total:
        usage_total[key] += int(usage.get(key, 0) or 0)
    edited_payload = _safe_json_object(edited_text)
    raw_proposals = edited_payload.get("proposals")
    proposals: list[ProposalItem] = []
    if isinstance(raw_proposals, list):
        seen_parent_ids: set[str] = set()
        for raw in raw_proposals:
            if not isinstance(raw, dict):
                continue
            parent_id = str(raw.get("parent_id") or raw.get("parentId") or "").strip()
            if not parent_id or parent_id in seen_parent_ids:
                continue
            original_chunk = exact_chunks_by_parent_id.get(parent_id)
            proposed = str(raw.get("proposed") or "")
            if original_chunk is None or not proposed.strip() or proposed.strip() == original_chunk.content.strip():
                continue
            seen_parent_ids.add(parent_id)
            proposals.append(
                ProposalItem(
                    fileId=file_id,
                    fileName=original_chunk.file_name,
                    parentId=parent_id,
                    original=original_chunk.content,
                    proposed=proposed,
                    source="agent",
                )
            )

    return (
        FileWorkerResult(
            file_id=file_id,
            file_name=file_name,
            proposals=proposals,
            explored_parent_ids=list(exact_chunks_by_parent_id.keys()),
            skipped=not bool(proposals),
            reason=None if proposals else str(edited_payload.get("reason") or "No proposal changed the target chunks.").strip(),
        ),
        usage_total,
        llm_calls,
    )


async def delegate_file_edits_tool(
    *,
    file_ids: list[str],
    instruction: str,
    user_id: str,
    included_file_ids: list[str] | None,
    parent_doc_cache: dict[str, dict[str, Any]],
    session: Any,
    timeout_s: float,
) -> tuple[list[FileWorkerResult], dict[str, int], int]:
    included_set = _included_file_ids_set(included_file_ids)
    normalized_file_ids: list[str] = []
    seen: set[str] = set()
    for raw_file_id in file_ids:
        file_id = str(raw_file_id or "").strip()
        if not file_id or file_id in seen:
            continue
        if included_set is not None and file_id not in included_set:
            continue
        seen.add(file_id)
        normalized_file_ids.append(file_id)

    semaphore = asyncio.Semaphore(_MAX_WORKER_CONCURRENCY)

    async def _run_with_limit(file_id: str):
        async with semaphore:
            return await _run_file_worker(
                file_id=file_id,
                instruction=instruction,
                user_id=user_id,
                included_file_ids=included_file_ids,
                parent_doc_cache=parent_doc_cache,
                session=session,
                timeout_s=timeout_s,
            )

    raw_results = await asyncio.gather(
        *[_run_with_limit(file_id) for file_id in normalized_file_ids],
        return_exceptions=True,
    )
    results: list[FileWorkerResult] = []
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    llm_calls = 0
    for file_id, raw_result in zip(normalized_file_ids, raw_results):
        if isinstance(raw_result, Exception):
            results.append(
                FileWorkerResult(
                    file_id=file_id,
                    file_name="unknown",
                    skipped=True,
                    reason=str(raw_result),
                )
            )
            continue
        worker_result, usage, calls = raw_result
        results.append(worker_result)
        llm_calls += int(calls or 0)
        for key in usage_total:
            usage_total[key] += int(usage.get(key, 0) or 0)
    return results, usage_total, llm_calls
