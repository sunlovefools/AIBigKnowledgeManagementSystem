import asyncio
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

fake_vectordb = types.ModuleType("app.vectordb.vectordb")
fake_vectordb.PARENT_STORE = types.SimpleNamespace(collection=None)


async def _unused_async(*args, **kwargs):
    return None


fake_vectordb.delete_children_by_file_id = _unused_async
fake_vectordb.delete_children_by_parent_id = _unused_async
fake_vectordb.delete_parent_documents_by_file_id = _unused_async
fake_vectordb.delete_parent_document = _unused_async
fake_vectordb.upsert_documents = _unused_async
sys.modules["app.vectordb.vectordb"] = fake_vectordb

from app.api import router_modifications
from app.service.modification import reconstruction_service as rs
from app.service.rag.ingestion.chunker import ChildChunkModel, ParentChunkModel


def test_reconstruction_service_delete_file_removes_db_rows_and_returns_s3_status(monkeypatch):
    async def _find_row(_file_id: str) -> dict:
        return {
            "_id": "parent-1",
            "value": {
                "page_content": "hello",
                "metadata": {
                    "file_metadata": {
                        "file_id": "file-1",
                        "file_name": "Report.pdf",
                    },
                    "parent_chunk_metadata": {
                        "parent_chunk_number": 0,
                    },
                },
            },
        }

    async def _delete_children(_file_id: str) -> int:
        return 7

    async def _delete_parents(_file_id: str) -> int:
        return 3

    monkeypatch.setattr(
        rs.ReconstructionService,
        "_find_first_parent_row_for_file_id",
        staticmethod(_find_row),
    )
    monkeypatch.setattr(rs, "delete_children_by_file_id", _delete_children)
    monkeypatch.setattr(rs, "delete_parent_documents_by_file_id", _delete_parents)
    monkeypatch.setattr(
        rs,
        "delete_docling_artifacts_by_file_id",
        lambda _file_id: {
            "s3Status": "deleted",
            "s3DeletedObjects": 5,
            "warnings": [],
        },
    )

    result = asyncio.run(rs.ReconstructionService.delete_file("file-1"))
    assert result["fileId"] == "file-1"
    assert result["fileName"] == "Report.pdf"
    assert result["deletedChildChunks"] == 7
    assert result["deletedParentChunks"] == 3
    assert result["s3Status"] == "deleted"
    assert result["s3DeletedObjects"] == 5


def test_reconstruction_service_delete_file_raises_when_missing(monkeypatch):
    async def _find_row(_file_id: str) -> None:
        return None

    monkeypatch.setattr(
        rs.ReconstructionService,
        "_find_first_parent_row_for_file_id",
        staticmethod(_find_row),
    )

    with pytest.raises(FileNotFoundError):
        asyncio.run(rs.ReconstructionService.delete_file("missing-file"))


def test_reconstruction_service_delete_file_keeps_success_when_s3_fails(monkeypatch):
    async def _find_row(_file_id: str) -> dict:
        return {
            "_id": "parent-1",
            "value": {
                "page_content": "hello",
                "metadata": {
                    "file_metadata": {
                        "file_id": "file-1",
                        "file_name": "Report.pdf",
                    },
                    "parent_chunk_metadata": {
                        "parent_chunk_number": 0,
                    },
                },
            },
        }

    monkeypatch.setattr(
        rs.ReconstructionService,
        "_find_first_parent_row_for_file_id",
        staticmethod(_find_row),
    )
    monkeypatch.setattr(rs, "delete_children_by_file_id", lambda _file_id: asyncio.sleep(0, result=2))
    monkeypatch.setattr(rs, "delete_parent_documents_by_file_id", lambda _file_id: asyncio.sleep(0, result=1))
    monkeypatch.setattr(
        rs,
        "delete_docling_artifacts_by_file_id",
        lambda _file_id: {
            "s3Status": "failed",
            "s3DeletedObjects": 0,
            "warnings": ["S3 cleanup failed for file_id=file-1: boom"],
        },
    )

    result = asyncio.run(rs.ReconstructionService.delete_file("file-1"))
    assert result["s3Status"] == "failed"
    assert result["warnings"] == ["S3 cleanup failed for file_id=file-1: boom"]


def test_reconstruction_service_delete_file_raises_when_parent_delete_fails(monkeypatch):
    async def _find_row(_file_id: str) -> dict:
        return {
            "_id": "parent-1",
            "value": {
                "page_content": "hello",
                "metadata": {
                    "file_metadata": {
                        "file_id": "file-1",
                        "file_name": "Report.pdf",
                    },
                    "parent_chunk_metadata": {
                        "parent_chunk_number": 0,
                    },
                },
            },
        }

    async def _delete_children(_file_id: str) -> int:
        return 2

    async def _delete_parents(_file_id: str) -> int:
        raise RuntimeError("parent delete failed")

    monkeypatch.setattr(
        rs.ReconstructionService,
        "_find_first_parent_row_for_file_id",
        staticmethod(_find_row),
    )
    monkeypatch.setattr(rs, "delete_children_by_file_id", _delete_children)
    monkeypatch.setattr(rs, "delete_parent_documents_by_file_id", _delete_parents)

    with pytest.raises(RuntimeError, match="File deletion failed"):
        asyncio.run(rs.ReconstructionService.delete_file("file-1"))


def test_delete_file_endpoint_returns_response(monkeypatch):
    async def _delete_file(*, file_id: str) -> dict:
        return {
            "fileId": file_id,
            "fileName": "Report.pdf",
            "deletedParentChunks": 3,
            "deletedChildChunks": 7,
            "s3Status": "not_found",
            "s3DeletedObjects": 0,
            "warnings": [],
        }

    monkeypatch.setattr(router_modifications.ReconstructionService, "delete_file", _delete_file)

    response = asyncio.run(router_modifications.delete_file_by_id("file-1"))
    assert response.fileId == "file-1"
    assert response.fileName == "Report.pdf"
    assert response.s3Status == "not_found"


def test_delete_file_endpoint_returns_404(monkeypatch):
    async def _delete_file(*, file_id: str) -> dict:
        raise FileNotFoundError(f"No parent chunks found for file_id={file_id}")

    monkeypatch.setattr(router_modifications.ReconstructionService, "delete_file", _delete_file)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(router_modifications.delete_file_by_id("missing-file"))

    assert exc.value.status_code == 404


def test_delete_file_endpoint_returns_503_on_runtime_error(monkeypatch):
    async def _delete_file(*, file_id: str) -> dict:
        raise RuntimeError(f"File deletion failed: {file_id}")

    monkeypatch.setattr(router_modifications.ReconstructionService, "delete_file", _delete_file)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(router_modifications.delete_file_by_id("file-1"))

    assert exc.value.status_code == 503


def test_reconstruction_service_update_file_noop_when_content_unchanged(monkeypatch):
    class _FakeCollection:
        def find(self, _query):
            return iter(
                [
                    {
                        "_id": "parent-2",
                        "value": {
                            "page_content": "second chunk",
                            "metadata": {
                                "file_metadata": {
                                    "file_id": "file-1",
                                    "file_name": "Report.pdf",
                                },
                                "parent_chunk_metadata": {
                                    "parent_chunk_number": 1,
                                },
                            },
                        },
                    },
                    {
                        "_id": "parent-1",
                        "value": {
                            "page_content": "first chunk",
                            "metadata": {
                                "file_metadata": {
                                    "file_id": "file-1",
                                    "file_name": "Report.pdf",
                                },
                                "parent_chunk_metadata": {
                                    "parent_chunk_number": 0,
                                },
                            },
                        },
                    },
                ]
            )

    monkeypatch.setattr(rs, "PARENT_STORE", types.SimpleNamespace(collection=_FakeCollection()))

    async def _unexpected_delete_children(_parent_id: str):
        raise AssertionError("delete_children_by_parent_id should not be called for no-op update")

    async def _unexpected_delete_parent(_parent_id: str):
        raise AssertionError("delete_parent_document should not be called for no-op update")

    def _unexpected_split(*args, **kwargs):
        raise AssertionError("split_parent_child_chunks should not be called for no-op update")

    monkeypatch.setattr(rs, "delete_children_by_parent_id", _unexpected_delete_children)
    monkeypatch.setattr(rs, "delete_parent_document", _unexpected_delete_parent)
    monkeypatch.setattr(rs, "split_parent_child_chunks", _unexpected_split)

    result = asyncio.run(
        rs.ReconstructionService.update_file(
            file_id="file-1",
            new_content="first chunk\n\nsecond chunk",
            file_name="Report.pdf",
        )
    )

    assert result["fileId"] == "file-1"
    assert result["previousFileId"] == "file-1"
    assert result["content"] == "first chunk\n\nsecond chunk"
    assert result["parentChunks"] == 2
    assert result["chunks"] == 0


def test_reconstruction_service_update_parent_chunks_batch_noop_when_all_updates_unchanged(monkeypatch):
    class _FakeCollection:
        def find(self, _query):
            return iter(
                [
                    {
                        "_id": "parent-1",
                        "value": {
                            "page_content": "chunk one",
                            "metadata": {
                                "file_metadata": {
                                    "file_id": "file-1",
                                    "file_name": "Report.md",
                                },
                                "parent_chunk_metadata": {
                                    "parent_chunk_number": 0,
                                },
                            },
                        },
                    },
                    {
                        "_id": "parent-2",
                        "value": {
                            "page_content": "chunk two",
                            "metadata": {
                                "file_metadata": {
                                    "file_id": "file-1",
                                    "file_name": "Report.md",
                                },
                                "parent_chunk_metadata": {
                                    "parent_chunk_number": 1000,
                                },
                            },
                        },
                    },
                ]
            )

    class _FakeParentStore:
        def __init__(self):
            self.collection = _FakeCollection()

        async def amset(self, _pairs):
            raise AssertionError("amset should not be called for all-unchanged batch update")

    monkeypatch.setattr(rs, "PARENT_STORE", _FakeParentStore())

    async def _unexpected_delete_children(_parent_id: str):
        raise AssertionError("delete_children_by_parent_id should not be called for unchanged batch update")

    async def _unexpected_delete_parent(_parent_id: str):
        raise AssertionError("delete_parent_document should not be called for unchanged batch update")

    async def _unexpected_upsert(*, parent_chunks, child_chunks):
        raise AssertionError("upsert_documents should not be called for unchanged batch update")

    def _unexpected_split(*args, **kwargs):
        raise AssertionError("split_parent_child_chunks_from_markdown should not be called for unchanged batch update")

    monkeypatch.setattr(rs, "delete_children_by_parent_id", _unexpected_delete_children)
    monkeypatch.setattr(rs, "delete_parent_document", _unexpected_delete_parent)
    monkeypatch.setattr(rs, "upsert_documents", _unexpected_upsert)
    monkeypatch.setattr(rs, "split_parent_child_chunks_from_markdown", _unexpected_split)

    result = asyncio.run(
        rs.ReconstructionService.update_parent_chunks_batch(
            file_id="file-1",
            file_name="Report.md",
            updates=[
                {"parentId": "parent-1", "content": "chunk one"},
                {"parentId": "parent-2", "content": "chunk two"},
            ],
        )
    )

    assert result["fileId"] == "file-1"
    assert result["updatedCount"] == 0
    assert result["results"] == []


def test_reconstruction_service_update_parent_chunks_batch_rewrites_only_touched_parents(monkeypatch):
    class _FakeCollection:
        def find(self, _query):
            return iter(
                [
                    {
                        "_id": "parent-1",
                        "value": {
                            "page_content": "chunk one",
                            "metadata": {
                                "file_metadata": {
                                    "file_id": "file-1",
                                    "file_name": "Report.md",
                                },
                                "parent_chunk_metadata": {
                                    "parent_chunk_number": 0,
                                },
                            },
                        },
                    },
                    {
                        "_id": "parent-2",
                        "value": {
                            "page_content": "chunk two",
                            "metadata": {
                                "file_metadata": {
                                    "file_id": "file-1",
                                    "file_name": "Report.md",
                                },
                                "parent_chunk_metadata": {
                                    "parent_chunk_number": 1000,
                                },
                            },
                        },
                    },
                ]
            )

    calls = {
        "split": [],
        "delete_children": [],
        "delete_parent": [],
        "upsert": [],
        "amset": [],
        "polish": [],
    }

    class _FakeParentStore:
        def __init__(self):
            self.collection = _FakeCollection()

        async def amset(self, pairs):
            calls["amset"].append(list(pairs))

    def _split_markdown(new_content: str, file_name: str, **kwargs):
        calls["split"].append(new_content)
        replacement_parent_id = "parent-1-replacement"
        parent_model = ParentChunkModel(
            parent_chunk_id=replacement_parent_id,
            content=new_content,
            file_metadata={"file_name": file_name, "file_id": kwargs.get("file_id", "file-1")},
            parent_chunk_metadata={
                "child_chunks_ids": ["child-r1"],
                "parent_chunk_number": 0,
                "page_number": [0],
                "ingested_at": "2026-01-01T00:00:00+00:00",
            },
            content_flags={"is_image": False, "is_table_image": False},
            artifact_refs={"image_uuid": [], "table_image_uuid": []},
        )
        child_model = ChildChunkModel(
            child_chunk_id="child-r1",
            content=f"child::{new_content}",
            file_metadata={"file_name": file_name, "file_id": kwargs.get("file_id", "file-1")},
            child_chunk_metadata={
                "parent_id": replacement_parent_id,
                "child_chunk_number": 0,
                "page_number": 0,
                "has_preamble": False,
                "ingested_at": "2026-01-01T00:00:00+00:00",
            },
            content_flags={"is_image": False, "is_table_image": False},
            artifact_refs={"image_uuid": None, "table_image_uuid": None},
        )
        return [parent_model], [child_model]

    async def _delete_children(parent_id: str):
        calls["delete_children"].append(parent_id)

    async def _delete_parent(parent_id: str):
        calls["delete_parent"].append(parent_id)

    async def _upsert_documents(*, parent_chunks, child_chunks):
        calls["upsert"].append({"parents": parent_chunks, "children": child_chunks})

    def _polish_chunks(child_chunks):
        calls["polish"].append(child_chunks)
        return child_chunks

    monkeypatch.setattr(rs, "PARENT_STORE", _FakeParentStore())
    monkeypatch.setattr(rs, "split_parent_child_chunks_from_markdown", _split_markdown)
    monkeypatch.setattr(rs, "delete_children_by_parent_id", _delete_children)
    monkeypatch.setattr(rs, "delete_parent_document", _delete_parent)
    monkeypatch.setattr(rs, "upsert_documents", _upsert_documents)
    monkeypatch.setattr(rs, "polish_chunks", _polish_chunks)

    result = asyncio.run(
        rs.ReconstructionService.update_parent_chunks_batch(
            file_id="file-1",
            file_name="Report.md",
            updates=[
                {"parentId": "parent-1", "content": "chunk one updated"},
                {"parentId": "parent-2", "content": "chunk two"},
            ],
        )
    )

    assert calls["split"] == ["chunk one updated"]
    assert calls["delete_children"] == ["parent-1"]
    assert calls["delete_parent"] == ["parent-1"]
    assert len(calls["upsert"]) == 1
    assert len(calls["amset"]) == 1

    upsert_payload = calls["upsert"][0]
    assert len(upsert_payload["parents"]) == 1
    assert len(upsert_payload["children"]) == 1
    assert upsert_payload["parents"][0]["parent_chunk_id"] == "parent-1-replacement"
    assert upsert_payload["children"][0]["child_chunk_id"] == "child-r1"

    untouched_pairs = calls["amset"][0]
    assert len(untouched_pairs) == 1
    assert untouched_pairs[0][0] == "parent-2"

    assert result["fileId"] == "file-1"
    assert result["updatedCount"] == 1
    assert result["results"][0]["previousParentId"] == "parent-1"
    assert result["results"][0]["parentId"] == "parent-1-replacement"
