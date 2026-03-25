import asyncio
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


stub_ingest_service = types.ModuleType("app.service.rag.ingestion.ingest_upload_service")


class UpsertChunksFailedError(RuntimeError):
    pass


async def _stub_upsert_chunks(*, parent_chunks, child_chunks, user_id):
    return None


async def _stub_run_ingest_upload(*, file_name, content_type, data):
    return {
        "parent_chunks": [],
        "child_chunks": [],
        "warnings": [],
    }


stub_ingest_service.UpsertChunksFailedError = UpsertChunksFailedError
stub_ingest_service.upsert_chunks = _stub_upsert_chunks
stub_ingest_service.run_ingest_upload = _stub_run_ingest_upload
sys.modules["app.service.rag.ingestion.ingest_upload_service"] = stub_ingest_service

from app.api import router_ingest


def test_upsert_chunks_helper_forwards_user_id(monkeypatch):
    captured = {}

    async def _fake_upsert_chunks(*, parent_chunks, child_chunks, user_id):
        captured["parent_chunks"] = parent_chunks
        captured["child_chunks"] = child_chunks
        captured["user_id"] = user_id

    monkeypatch.setattr(router_ingest.ingest_upload_service, "upsert_chunks", _fake_upsert_chunks)

    asyncio.run(
        router_ingest._upsert_chunks(
            parent_chunks=[{"parent_chunk_id": "p1"}],
            child_chunks=[{"child_chunk_id": "c1"}],
            user_id="user-1",
        )
    )

    assert captured["parent_chunks"] == [{"parent_chunk_id": "p1"}]
    assert captured["child_chunks"] == [{"child_chunk_id": "c1"}]
    assert captured["user_id"] == "user-1"


def test_ingest_upload_route_threads_jwt_sub_into_upsert(monkeypatch):
    captured = {}

    async def _fake_run_ingest_upload(*, file_name, content_type, data):
        captured["run_file_name"] = file_name
        captured["run_content_type"] = content_type
        captured["run_data"] = data
        return {
            "parent_chunks": [{"parent_chunk_id": "p1"}],
            "child_chunks": [{"child_chunk_id": "c1"}],
            "warnings": [],
        }

    async def _fake_upsert_chunks(parent_chunks, child_chunks, user_id):
        captured["upsert_parent_chunks"] = parent_chunks
        captured["upsert_child_chunks"] = child_chunks
        captured["upsert_user_id"] = user_id

    monkeypatch.setattr(router_ingest.ingest_upload_service, "run_ingest_upload", _fake_run_ingest_upload)
    monkeypatch.setattr(router_ingest, "_upsert_chunks", _fake_upsert_chunks)

    payload = router_ingest.FileUpload(
        fileName="example.md",
        contentType="text/markdown",
        data="SGVsbG8=",
    )

    response = asyncio.run(
        router_ingest.ingest_upload(payload, current_user={"sub": "user-1"})
    )

    assert response.status == "ok"
    assert response.file_name == "example.md"
    assert response.parent_chunks == 1
    assert response.child_chunks == 1
    assert captured["upsert_user_id"] == "user-1"


def test_ingest_upload_route_rejects_missing_user_id():
    payload = router_ingest.FileUpload(
        fileName="example.md",
        contentType="text/markdown",
        data="SGVsbG8=",
    )

    with pytest.raises(HTTPException, match="Authentication required"):
        asyncio.run(router_ingest.ingest_upload(payload, current_user={}))
