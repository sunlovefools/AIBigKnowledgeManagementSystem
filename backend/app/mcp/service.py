"""Read-only RAG operations used by the MCP tool layer."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from .auth import get_current_user_id
from .models import (
    CollectionDescriptor,
    CollectionListResponse,
    CollectionSummary,
    DescribeCollectionResponse,
    EvidenceItem,
    FetchParentChunkResponse,
    FileOutlineChunk,
    FileOutlineResponse,
    FileSearchResponse,
    FileSummary,
    ParentChunkContent,
    SearchMaterialsResponse,
    SearchScope,
)

_MAX_TOP_K = 20
_MAX_FILES = 100
_MAX_FILE_LIMIT = 20
_MAX_OUTLINE_CHUNKS = 80
_MAX_PARENT_CHARS = 20_000
_DEFAULT_SNIPPET_CHARS = 1400
_DEFAULT_PREVIEW_CHARS = 420


def _get_collection_service() -> Any:
    from app.service.collection.collection_service import CollectionService

    return CollectionService


def _bounded_int(raw: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _safe_int(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def _compact_text(raw: str, *, max_chars: int, query: str | None = None) -> str:
    compact = " ".join(str(raw or "").split())
    if len(compact) <= max_chars:
        return compact

    normalized_query = " ".join(str(query or "").split()).lower()
    start_index = -1
    if normalized_query:
        start_index = compact.lower().find(normalized_query)

    if start_index < 0 and normalized_query:
        for term in re.split(r"\W+", normalized_query):
            if len(term) < 4:
                continue
            start_index = compact.lower().find(term)
            if start_index >= 0:
                break

    if start_index < 0:
        return compact[:max_chars].rstrip()

    window_start = max(0, start_index - max_chars // 3)
    window_end = min(len(compact), window_start + max_chars)
    snippet = compact[window_start:window_end]
    if window_start > 0:
        snippet = "... " + snippet
    if window_end < len(compact):
        snippet += " ..."
    return snippet


def _heading_from_content(content: str) -> str | None:
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            return line.lstrip("#").strip()[:160] or None
        return line[:160]
    return None


def _collection_summary(row: dict[str, Any]) -> CollectionSummary:
    return CollectionSummary(
        collectionId=str(row.get("collection_id") or ""),
        name=str(row.get("name") or ""),
        isDefault=bool(row.get("is_default", False)),
        fileCount=max(int(row.get("file_count") or 0), 0),
    )


def _collection_descriptor(row: dict[str, Any]) -> CollectionDescriptor:
    return CollectionDescriptor(**_collection_summary(row).model_dump())


async def _resolve_collection_scope(
    *,
    user_id: str,
    collection_id: str | None,
) -> tuple[CollectionDescriptor, list[str]]:
    collection_service = _get_collection_service()
    active = await collection_service.resolve_active_collection(
        user_id=user_id,
        requested_collection_id=collection_id,
    )
    active_id = str(active.get("collection_id") or "").strip()
    file_ids = await collection_service.list_file_ids_for_collection(
        user_id=user_id,
        collection_id=active_id,
    )
    return _collection_descriptor(active), [
        str(file_id).strip()
        for file_id in file_ids
        if str(file_id).strip()
    ]


def _get_reconstruction_service() -> Any:
    from app.service.modification.reconstruction_service import ReconstructionService

    return ReconstructionService


def _get_parent_store() -> Any:
    from app.vectordb.vectordb import PARENT_STORE

    return PARENT_STORE


def _get_vector_search() -> Any:
    from app.vectordb.vectordb import search_and_retrieve_context

    return search_and_retrieve_context


def _normalize_file_summaries(raw_files: list[dict[str, Any]]) -> list[FileSummary]:
    files: list[FileSummary] = []
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("fileId") or item.get("file_id") or "").strip()
        file_name = str(item.get("fileName") or item.get("file_name") or "unknown").strip() or "unknown"
        if not file_id:
            continue
        files.append(
            FileSummary(
                fileId=file_id,
                fileName=file_name,
                preview=str(item.get("preview") or item.get("previewTexts") or ""),
            )
        )
    return files


async def list_user_collections() -> CollectionListResponse:
    user_id = get_current_user_id()
    collection_service = _get_collection_service()
    rows = await collection_service.list_collections(user_id)
    collections = [_collection_summary(row) for row in rows]
    return CollectionListResponse(collections=collections, total=len(collections))


async def describe_user_collection(
    *,
    collection_id: str | None,
    max_files: int,
) -> DescribeCollectionResponse:
    user_id = get_current_user_id()
    limit = _bounded_int(max_files, default=_MAX_FILES, minimum=1, maximum=_MAX_FILES)
    collection_service = _get_collection_service()
    active = await collection_service.resolve_active_collection(
        user_id=user_id,
        requested_collection_id=collection_id,
    )
    reconstruction = _get_reconstruction_service()
    raw_files = await reconstruction.get_all_preview_files(
        user_id=user_id,
        collection_id=str(active.get("collection_id") or ""),
    )
    files = _normalize_file_summaries(raw_files)
    return DescribeCollectionResponse(
        collection=_collection_descriptor(active),
        files=files[:limit],
        total=len(files),
        truncated=len(files) > limit,
    )


def _extract_doc_metadata(doc: dict[str, Any]) -> dict[str, Any]:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    file_metadata = (
        metadata.get("file_metadata")
        if isinstance(metadata.get("file_metadata"), dict)
        else {}
    )
    parent_metadata = (
        metadata.get("parent_chunk_metadata")
        if isinstance(metadata.get("parent_chunk_metadata"), dict)
        else {}
    )
    collection_metadata = (
        metadata.get("collection_metadata")
        if isinstance(metadata.get("collection_metadata"), dict)
        else {}
    )
    return {
        "parentId": str(doc.get("id") or doc.get("_id") or "").strip(),
        "userId": str(metadata.get("user_id") or "").strip(),
        "fileId": str(file_metadata.get("file_id") or metadata.get("file_id") or "").strip(),
        "fileName": str(file_metadata.get("file_name") or metadata.get("source") or "unknown").strip() or "unknown",
        "parentChunkNumber": _safe_int(parent_metadata.get("parent_chunk_number")),
        "collectionId": str(collection_metadata.get("collection_id") or "").strip() or None,
        "collectionName": str(collection_metadata.get("collection_name") or "").strip() or None,
    }


def _normalize_parent_store_row(row: dict[str, Any]) -> dict[str, Any] | None:
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
    return doc


def _doc_is_authorized(
    doc: dict[str, Any],
    *,
    user_id: str,
    included_file_ids: set[str] | None,
) -> bool:
    meta = _extract_doc_metadata(doc)
    if meta["userId"] != user_id:
        return False
    if not meta["fileId"]:
        return False
    if included_file_ids is not None and meta["fileId"] not in included_file_ids:
        return False
    return True


async def search_user_materials(
    *,
    query: str,
    collection_id: str | None,
    search_scope: SearchScope,
    top_k: int,
) -> SearchMaterialsResponse:
    user_id = get_current_user_id()
    normalized_query = str(query or "").strip()
    if not normalized_query:
        raise ValueError("query must not be empty.")

    bounded_top_k = _bounded_int(top_k, default=8, minimum=1, maximum=_MAX_TOP_K)
    included_file_ids: list[str] | None
    active_collection: CollectionDescriptor | None
    if search_scope == "all_collections":
        if str(collection_id or "").strip():
            raise ValueError("collectionId must be omitted when searchScope is all_collections.")
        included_file_ids = None
        active_collection = None
    else:
        active_collection, included_file_ids = await _resolve_collection_scope(
            user_id=user_id,
            collection_id=collection_id,
        )
        if not included_file_ids:
            return SearchMaterialsResponse(
                query=normalized_query,
                searchScope=search_scope,
                collection=active_collection,
                evidence=[],
                total=0,
            )

    vector_search = _get_vector_search()
    docs = await vector_search(
        query=normalized_query,
        top_k=bounded_top_k,
        user_id=user_id,
        included_file_ids=included_file_ids,
    )

    included_set = None if included_file_ids is None else set(included_file_ids)
    evidence: list[EvidenceItem] = []
    for doc in docs if isinstance(docs, list) else []:
        if not isinstance(doc, dict) or not _doc_is_authorized(
            doc,
            user_id=user_id,
            included_file_ids=included_set,
        ):
            continue
        meta = _extract_doc_metadata(doc)
        if not meta["parentId"]:
            continue
        evidence.append(
            EvidenceItem(
                parentId=meta["parentId"],
                fileId=meta["fileId"],
                fileName=meta["fileName"],
                collectionId=meta["collectionId"],
                collectionName=meta["collectionName"],
                parentChunkNumber=meta["parentChunkNumber"],
                snippet=_compact_text(
                    str(doc.get("page_content") or ""),
                    max_chars=_DEFAULT_SNIPPET_CHARS,
                    query=normalized_query,
                ),
            )
        )
    return SearchMaterialsResponse(
        query=normalized_query,
        searchScope=search_scope,
        collection=active_collection,
        evidence=evidence,
        total=len(evidence),
    )


def _file_matches_query(file: FileSummary, query: str) -> bool:
    normalized_query = str(query or "").strip().lower()
    terms = [term for term in re.split(r"\W+", normalized_query) if len(term) >= 2]
    if not terms:
        return True
    haystack = f"{file.fileName} {file.preview}".lower()
    return all(term in haystack for term in terms) or any(
        len(term) >= 4 and term in haystack for term in terms
    )


async def search_user_files(
    *,
    query: str,
    collection_id: str | None,
    limit: int,
) -> FileSearchResponse:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        raise ValueError("query must not be empty.")

    described = await describe_user_collection(
        collection_id=collection_id,
        max_files=_MAX_FILES,
    )
    bounded_limit = _bounded_int(limit, default=10, minimum=1, maximum=_MAX_FILE_LIMIT)
    matches = [file for file in described.files if _file_matches_query(file, normalized_query)]
    return FileSearchResponse(
        query=normalized_query,
        collection=described.collection,
        files=matches[:bounded_limit],
        total=len(matches),
        truncated=len(matches) > bounded_limit,
    )


async def fetch_user_parent_chunk(
    *,
    parent_id: str,
    collection_id: str | None,
    max_chars: int,
) -> FetchParentChunkResponse:
    user_id = get_current_user_id()
    normalized_parent_id = str(parent_id or "").strip()
    if not normalized_parent_id:
        raise ValueError("parentId must not be empty.")

    active, included_file_ids = await _resolve_collection_scope(
        user_id=user_id,
        collection_id=collection_id,
    )
    included_set = set(included_file_ids)
    if not included_set:
        return FetchParentChunkResponse(parentChunk=None)

    parent_store = _get_parent_store()
    raw_docs = await parent_store.amget([normalized_parent_id])
    if not isinstance(raw_docs, list) or not raw_docs or not isinstance(raw_docs[0], dict):
        return FetchParentChunkResponse(parentChunk=None)

    doc = dict(raw_docs[0])
    doc["id"] = normalized_parent_id
    if not _doc_is_authorized(doc, user_id=user_id, included_file_ids=included_set):
        return FetchParentChunkResponse(parentChunk=None)

    meta = _extract_doc_metadata(doc)
    content = str(doc.get("page_content") or "")
    bounded_max_chars = _bounded_int(
        max_chars,
        default=6000,
        minimum=500,
        maximum=_MAX_PARENT_CHARS,
    )
    truncated = len(content) > bounded_max_chars
    return FetchParentChunkResponse(
        parentChunk=ParentChunkContent(
            parentId=normalized_parent_id,
            fileId=meta["fileId"],
            fileName=meta["fileName"],
            collectionId=meta["collectionId"] or active.collectionId,
            collectionName=meta["collectionName"] or active.name,
            parentChunkNumber=meta["parentChunkNumber"],
            content=content[:bounded_max_chars],
            truncated=truncated,
        )
    )


async def fetch_user_file_outline(
    *,
    file_id: str,
    collection_id: str | None,
    max_chunks: int,
) -> FileOutlineResponse:
    user_id = get_current_user_id()
    normalized_file_id = str(file_id or "").strip()
    if not normalized_file_id:
        raise ValueError("fileId must not be empty.")

    active, included_file_ids = await _resolve_collection_scope(
        user_id=user_id,
        collection_id=collection_id,
    )
    included_set = set(included_file_ids)
    if normalized_file_id not in included_set:
        return FileOutlineResponse(fileId=normalized_file_id, collection=active, chunks=[], total=0)

    parent_store = _get_parent_store()

    def _query_rows() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in parent_store.collection.find(
            {
                "value.metadata.user_id": user_id,
                "value.metadata.file_metadata.file_id": normalized_file_id,
            },
            projection={"_id": True, "value": True},
        ):
            if isinstance(row, dict):
                rows.append(row)
        return rows

    rows = await asyncio.to_thread(_query_rows)
    docs: list[dict[str, Any]] = []
    for row in rows:
        doc = _normalize_parent_store_row(row)
        if doc is None or not _doc_is_authorized(
            doc,
            user_id=user_id,
            included_file_ids=included_set,
        ):
            continue
        docs.append(doc)

    docs.sort(
        key=lambda doc: (
            _extract_doc_metadata(doc)["parentChunkNumber"] is None,
            _extract_doc_metadata(doc)["parentChunkNumber"] or 0,
            str(doc.get("id") or ""),
        )
    )

    bounded_max_chunks = _bounded_int(
        max_chunks,
        default=40,
        minimum=1,
        maximum=_MAX_OUTLINE_CHUNKS,
    )
    chunks: list[FileOutlineChunk] = []
    for doc in docs[:bounded_max_chunks]:
        meta = _extract_doc_metadata(doc)
        content = str(doc.get("page_content") or "")
        chunks.append(
            FileOutlineChunk(
                parentId=meta["parentId"],
                fileId=meta["fileId"],
                fileName=meta["fileName"],
                collectionId=meta["collectionId"] or active.collectionId,
                collectionName=meta["collectionName"] or active.name,
                parentChunkNumber=meta["parentChunkNumber"],
                heading=_heading_from_content(content),
                preview=_compact_text(content, max_chars=_DEFAULT_PREVIEW_CHARS),
                size=len(content),
            )
        )

    return FileOutlineResponse(
        fileId=normalized_file_id,
        collection=active,
        chunks=chunks,
        total=len(docs),
        truncated=len(docs) > bounded_max_chunks,
    )
