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
