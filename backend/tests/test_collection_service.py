import asyncio
import sys
import types
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.service.collection import collection_service as cs


def _get_nested_value(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for segment in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def _matches_query(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if _get_nested_value(document, key) != expected:
            return False
    return True


class _FakeCollectionStore:
    def __init__(self):
        self.rows: list[dict[str, Any]] = []

    def find_one(self, query: dict[str, Any]):
        for row in self.rows:
            if _matches_query(row, query):
                return row
        return None

    def insert_one(self, row: dict[str, Any]):
        self.rows.append(dict(row))
        return types.SimpleNamespace(inserted_id=row.get("collection_id"))

    def find(self, query: dict[str, Any]):
        return iter([row for row in self.rows if _matches_query(row, query)])

    def update_one(self, filter_doc: dict[str, Any], update_doc: dict[str, Any]):
        row = self.find_one(filter_doc)
        if row is None:
            return types.SimpleNamespace(matched_count=0, modified_count=0)

        set_doc = update_doc.get("$set", {})
        if isinstance(set_doc, dict):
            row.update(set_doc)
        return types.SimpleNamespace(matched_count=1, modified_count=1)

    def delete_one(self, filter_doc: dict[str, Any]):
        for index, row in enumerate(self.rows):
            if _matches_query(row, filter_doc):
                self.rows.pop(index)
                return types.SimpleNamespace(deleted_count=1)
        return types.SimpleNamespace(deleted_count=0)


class _FakeParentCollection:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    def find(self, query: dict[str, Any]):
        return iter([row for row in self.rows if _matches_query(row, query)])


def test_collection_service_create_list_and_uniqueness(monkeypatch):
    store = _FakeCollectionStore()
    monkeypatch.setattr(cs, "get_user_collections_collection", lambda: store)

    listed = asyncio.run(cs.CollectionService.list_collections("user-1"))
    assert len(listed) == 1
    assert listed[0]["is_default"] is True

    created = asyncio.run(cs.CollectionService.create_collection("user-1", "Project A"))
    assert created["name"] == "Project A"
    assert created["is_default"] is False

    listed_after_create = asyncio.run(cs.CollectionService.list_collections("user-1"))
    assert [entry["name"] for entry in listed_after_create] == ["Default", "Project A"]

    with pytest.raises(cs.CollectionConflictError):
        asyncio.run(cs.CollectionService.create_collection("user-1", "project a"))


def test_collection_service_ensure_default_collection_deduplicates_existing_defaults(monkeypatch):
    store = _FakeCollectionStore()
    store.rows = [
        {
            "collection_id": "default-2",
            "user_id": "user-1",
            "name": "Default",
            "normalized_name": "default",
            "is_default": True,
            "file_count": 0,
            "created_at": "2026-02-01T00:00:00+00:00",
            "updated_at": "2026-02-01T00:00:00+00:00",
        },
        {
            "collection_id": "default-1",
            "user_id": "user-1",
            "name": "Default",
            "normalized_name": "default",
            "is_default": True,
            "file_count": 0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    ]
    monkeypatch.setattr(cs, "get_user_collections_collection", lambda: store)

    default_row = asyncio.run(cs.CollectionService.ensure_default_collection("user-1"))
    assert default_row["collection_id"] == "default-1"

    listed = asyncio.run(cs.CollectionService.list_collections("user-1"))
    assert len(listed) == 1
    assert listed[0]["collection_id"] == "default-1"


def test_collection_service_list_file_ids_scopes_with_default_fallback(monkeypatch):
    store = _FakeCollectionStore()
    monkeypatch.setattr(cs, "get_user_collections_collection", lambda: store)

    default_collection = asyncio.run(cs.CollectionService.ensure_default_collection("user-1"))
    project_collection = asyncio.run(cs.CollectionService.create_collection("user-1", "Project Scope"))

    rows = [
        {
            "_id": "parent-1",
            "value": {
                "metadata": {
                    "user_id": "user-1",
                    "file_metadata": {"file_id": "file-default"},
                }
            },
        },
        {
            "_id": "parent-2",
            "value": {
                "metadata": {
                    "user_id": "user-1",
                    "file_metadata": {"file_id": "file-project"},
                    "collection_metadata": {"collection_id": project_collection["collection_id"]},
                }
            },
        },
        {
            "_id": "parent-3",
            "value": {
                "metadata": {
                    "user_id": "user-1",
                    "file_metadata": {"file_id": "file-other"},
                    "collection_metadata": {"collection_id": "another-collection"},
                }
            },
        },
        {
            "_id": "parent-4",
            "value": {
                "metadata": {
                    "user_id": "user-2",
                    "file_metadata": {"file_id": "file-other-user"},
                }
            },
        },
    ]
    fake_vectordb = types.ModuleType("app.vectordb.vectordb")
    fake_vectordb.PARENT_STORE = types.SimpleNamespace(collection=_FakeParentCollection(rows))
    monkeypatch.setitem(sys.modules, "app.vectordb.vectordb", fake_vectordb)

    default_ids = asyncio.run(
        cs.CollectionService.list_file_ids_for_collection("user-1", default_collection["collection_id"])
    )
    project_ids = asyncio.run(
        cs.CollectionService.list_file_ids_for_collection("user-1", project_collection["collection_id"])
    )

    assert default_ids == ["file-default"]
    assert project_ids == ["file-project"]


def test_collection_service_delete_collection_cascades_and_blocks_default(monkeypatch):
    store = _FakeCollectionStore()
    monkeypatch.setattr(cs, "get_user_collections_collection", lambda: store)

    default_collection = asyncio.run(cs.CollectionService.ensure_default_collection("user-1"))
    project_collection = asyncio.run(cs.CollectionService.create_collection("user-1", "To Delete"))

    rows = [
        {
            "_id": "parent-1",
            "value": {
                "metadata": {
                    "user_id": "user-1",
                    "file_metadata": {"file_id": "file-a"},
                    "collection_metadata": {"collection_id": project_collection["collection_id"]},
                }
            },
        },
        {
            "_id": "parent-2",
            "value": {
                "metadata": {
                    "user_id": "user-1",
                    "file_metadata": {"file_id": "file-b"},
                    "collection_metadata": {"collection_id": project_collection["collection_id"]},
                }
            },
        },
    ]
    calls: list[tuple[str, str, str]] = []

    async def _delete_children(file_id: str, user_id: str):
        calls.append(("children", file_id, user_id))
        return 3

    async def _delete_parents(file_id: str, user_id: str):
        calls.append(("parents", file_id, user_id))
        return 2

    fake_vectordb = types.ModuleType("app.vectordb.vectordb")
    fake_vectordb.PARENT_STORE = types.SimpleNamespace(collection=_FakeParentCollection(rows))
    fake_vectordb.delete_children_by_file_id = _delete_children
    fake_vectordb.delete_parent_documents_by_file_id = _delete_parents
    monkeypatch.setitem(sys.modules, "app.vectordb.vectordb", fake_vectordb)

    with pytest.raises(cs.ProtectedCollectionError):
        asyncio.run(cs.CollectionService.delete_collection("user-1", default_collection["collection_id"]))

    deleted = asyncio.run(
        cs.CollectionService.delete_collection("user-1", project_collection["collection_id"])
    )
    assert deleted["deleted_files"] == 2
    assert deleted["deleted_child_chunks"] == 6
    assert deleted["deleted_parent_chunks"] == 4
    assert len(calls) == 4

    listed_after_delete = asyncio.run(cs.CollectionService.list_collections("user-1"))
    assert len(listed_after_delete) == 1
    assert listed_after_delete[0]["collection_id"] == default_collection["collection_id"]
