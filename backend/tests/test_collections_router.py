import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api import router_collections


def test_list_collections_route_returns_user_scoped_rows(monkeypatch):
    async def _list_collections(user_id: str):
        assert user_id == "user-1"
        return [
            {
                "collection_id": "collection-default",
                "name": "Default",
                "is_default": True,
                "file_count": 2,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-02T00:00:00+00:00",
            }
        ]

    monkeypatch.setattr(router_collections.CollectionService, "list_collections", _list_collections)

    response = asyncio.run(router_collections.list_collections(current_user={"sub": "user-1"}))
    assert response.total == 1
    assert response.collections[0].collectionId == "collection-default"
    assert response.collections[0].isDefault is True


def test_create_collection_route_maps_conflict(monkeypatch):
    async def _create_collection(user_id: str, name: str):
        _ = user_id, name
        raise router_collections.CollectionConflictError("Collection already exists.")

    monkeypatch.setattr(router_collections.CollectionService, "create_collection", _create_collection)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            router_collections.create_collection(
                router_collections.CreateCollectionRequest(name="Team Docs"),
                current_user={"sub": "user-1"},
            )
        )

    assert exc.value.status_code == 409


def test_delete_collection_route_maps_default_protection(monkeypatch):
    async def _delete_collection(user_id: str, collection_id: str):
        _ = user_id, collection_id
        raise router_collections.ProtectedCollectionError("Default collection cannot be deleted.")

    monkeypatch.setattr(router_collections.CollectionService, "delete_collection", _delete_collection)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(router_collections.delete_collection("collection-default", current_user={"sub": "user-1"}))

    assert exc.value.status_code == 409
