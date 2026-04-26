import asyncio
import types
from typing import Any

from app.mcp import service as mcp_service


class _FakeCollectionService:
    collections = [
        {
            "collection_id": "collection-default",
            "name": "Default",
            "is_default": True,
            "file_count": 1,
        },
        {
            "collection_id": "collection-project",
            "name": "Project",
            "is_default": False,
            "file_count": 1,
        },
    ]
    files_by_collection = {
        "collection-default": ["file-a"],
        "collection-project": ["file-b"],
    }

    @staticmethod
    async def list_collections(user_id: str):
        assert user_id == "user-1"
        return list(_FakeCollectionService.collections)

    @staticmethod
    async def resolve_active_collection(user_id: str, requested_collection_id: str | None = None):
        assert user_id == "user-1"
        target = requested_collection_id or "collection-default"
        for row in _FakeCollectionService.collections:
            if row["collection_id"] == target:
                return dict(row)
        raise RuntimeError("collection not found")

    @staticmethod
    async def list_file_ids_for_collection(user_id: str, collection_id: str):
        assert user_id == "user-1"
        return list(_FakeCollectionService.files_by_collection.get(collection_id, []))


class _FakeReconstructionService:
    @staticmethod
    async def get_all_preview_files(user_id: str, collection_id: str | None = None):
        assert user_id == "user-1"
        if collection_id == "collection-project":
            return [{"fileId": "file-b", "fileName": "meeting.md", "preview": "Meeting notes"}]
        return [
            {"fileId": "file-a", "fileName": "policy.md", "preview": "Refund policy"},
            {"fileId": "file-c", "fileName": "minutes.md", "preview": "Absentees and minutes"},
        ]


class _FakeParentCollection:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    def find(self, filter: dict[str, Any], projection: dict[str, Any] | None = None):
        _ = projection
        user_id = filter["value.metadata.user_id"]
        file_id = filter["value.metadata.file_metadata.file_id"]
        return iter(
            [
                row
                for row in self.rows
                if row["value"]["metadata"]["user_id"] == user_id
                and row["value"]["metadata"]["file_metadata"]["file_id"] == file_id
            ]
        )


class _FakeParentStore:
    def __init__(self, rows: list[dict[str, Any]], docs_by_id: dict[str, dict[str, Any]]):
        self.collection = _FakeParentCollection(rows)
        self.docs_by_id = docs_by_id

    async def amget(self, parent_ids: list[str]):
        return [self.docs_by_id.get(parent_id) for parent_id in parent_ids]


def _patch_common(monkeypatch):
    monkeypatch.setattr(mcp_service, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(mcp_service, "_get_collection_service", lambda: _FakeCollectionService)
    monkeypatch.setattr(mcp_service, "_get_reconstruction_service", lambda: _FakeReconstructionService)


def _doc(
    parent_id: str,
    *,
    user_id: str = "user-1",
    file_id: str = "file-a",
    file_name: str = "policy.md",
    chunk_number: int = 0,
    content: str = "Policy content",
    collection_id: str = "collection-default",
    collection_name: str = "Default",
):
    return {
        "id": parent_id,
        "page_content": content,
        "metadata": {
            "user_id": user_id,
            "file_metadata": {"file_id": file_id, "file_name": file_name},
            "parent_chunk_metadata": {"parent_chunk_number": chunk_number},
            "collection_metadata": {
                "collection_id": collection_id,
                "collection_name": collection_name,
            },
        },
    }


def _row(parent_id: str, **kwargs):
    doc = _doc(parent_id, **kwargs)
    row_doc = dict(doc)
    row_doc.pop("id", None)
    return {"_id": parent_id, "value": row_doc}


def test_list_and_describe_collections_are_bounded(monkeypatch):
    _patch_common(monkeypatch)

    listed = asyncio.run(mcp_service.list_user_collections())
    described = asyncio.run(
        mcp_service.describe_user_collection(collection_id=None, max_files=1)
    )

    assert listed.total == 2
    assert listed.collections[0].collectionId == "collection-default"
    assert described.collection.collectionId == "collection-default"
    assert described.total == 2
    assert described.truncated is True
    assert [file.fileId for file in described.files] == ["file-a"]


def test_search_files_uses_filename_and_preview(monkeypatch):
    _patch_common(monkeypatch)

    response = asyncio.run(
        mcp_service.search_user_files(
            query="absentees",
            collection_id=None,
            limit=10,
        )
    )

    assert response.total == 1
    assert response.files[0].fileId == "file-c"


def test_search_materials_enforces_collection_scope_and_ownership(monkeypatch):
    _patch_common(monkeypatch)
    captured: dict[str, Any] = {}

    async def _fake_vector_search(**kwargs):
        captured.update(kwargs)
        return [
            _doc("parent-a", file_id="file-a", content="Relevant refund policy"),
            _doc("parent-b", user_id="user-2", file_id="file-a", content="Other user"),
            _doc("parent-c", file_id="file-c", content="Out of collection"),
        ]

    monkeypatch.setattr(mcp_service, "_get_vector_search", lambda: _fake_vector_search)

    response = asyncio.run(
        mcp_service.search_user_materials(
            query="refund",
            collection_id=None,
            search_scope="collection",
            top_k=99,
        )
    )

    assert captured["included_file_ids"] == ["file-a"]
    assert captured["top_k"] == 20
    assert response.total == 1
    assert response.evidence[0].parentId == "parent-a"


def test_search_materials_all_collections_skips_file_filter(monkeypatch):
    _patch_common(monkeypatch)
    captured: dict[str, Any] = {}

    async def _fake_vector_search(**kwargs):
        captured.update(kwargs)
        return [_doc("parent-a", file_id="file-a", content="Relevant refund policy")]

    monkeypatch.setattr(mcp_service, "_get_vector_search", lambda: _fake_vector_search)

    response = asyncio.run(
        mcp_service.search_user_materials(
            query="refund",
            collection_id=None,
            search_scope="all_collections",
            top_k=8,
        )
    )

    assert captured["included_file_ids"] is None
    assert response.collection is None
    assert response.total == 1


def test_search_materials_all_collections_rejects_collection_id(monkeypatch):
    _patch_common(monkeypatch)

    try:
        asyncio.run(
            mcp_service.search_user_materials(
                query="refund",
                collection_id="collection-default",
                search_scope="all_collections",
                top_k=8,
            )
        )
    except ValueError as exc:
        assert "collectionId must be omitted" in str(exc)
    else:
        raise AssertionError("Expected all_collections with collectionId to fail")


def test_fetch_parent_chunk_enforces_scope_and_truncates(monkeypatch):
    _patch_common(monkeypatch)
    parent_store = _FakeParentStore(
        rows=[],
        docs_by_id={
            "parent-a": _doc("parent-a", content="x" * 700),
            "parent-b": _doc("parent-b", file_id="file-b", content="project content"),
        },
    )
    monkeypatch.setattr(mcp_service, "_get_parent_store", lambda: parent_store)

    response = asyncio.run(
        mcp_service.fetch_user_parent_chunk(
            parent_id="parent-a",
            collection_id=None,
            max_chars=500,
        )
    )
    out_of_scope = asyncio.run(
        mcp_service.fetch_user_parent_chunk(
            parent_id="parent-b",
            collection_id=None,
            max_chars=500,
        )
    )

    assert response.parentChunk is not None
    assert len(response.parentChunk.content) == 500
    assert response.parentChunk.truncated is True
    assert out_of_scope.parentChunk is None


def test_fetch_file_outline_is_scoped_sorted_and_bounded(monkeypatch):
    _patch_common(monkeypatch)
    rows = [
        _row("parent-2", chunk_number=2, content="## Later\nMore details"),
        _row("parent-1", chunk_number=1, content="# First\nOpening details"),
        _row("parent-other", user_id="user-2", chunk_number=0, content="Other user"),
    ]
    parent_store = _FakeParentStore(rows=rows, docs_by_id={})
    monkeypatch.setattr(mcp_service, "_get_parent_store", lambda: parent_store)

    response = asyncio.run(
        mcp_service.fetch_user_file_outline(
            file_id="file-a",
            collection_id=None,
            max_chunks=1,
        )
    )

    assert response.total == 2
    assert response.truncated is True
    assert len(response.chunks) == 1
    assert response.chunks[0].parentId == "parent-1"
    assert response.chunks[0].heading == "First"


def test_fetch_file_outline_returns_empty_for_out_of_scope_file(monkeypatch):
    _patch_common(monkeypatch)
    parent_store = _FakeParentStore(rows=[], docs_by_id={})
    monkeypatch.setattr(mcp_service, "_get_parent_store", lambda: parent_store)

    response = asyncio.run(
        mcp_service.fetch_user_file_outline(
            file_id="file-b",
            collection_id=None,
            max_chunks=10,
        )
    )

    assert response.total == 0
    assert response.chunks == []
