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


def _make_parent_row(
    *,
    parent_id: str,
    content: str,
    chunk_number: int,
    file_id: str = "file-1",
    file_name: str = "Report.md",
) -> dict:
    return {
        "_id": parent_id,
        "value": {
            "page_content": content,
            "metadata": {
                "file_metadata": {
                    "file_id": file_id,
                    "file_name": file_name,
                },
                "parent_chunk_metadata": {
                    "parent_chunk_number": chunk_number,
                },
            },
        },
    }


def _make_parent_model(
    *,
    parent_id: str,
    content: str,
    chunk_number: int,
    child_id: str,
    file_id: str = "file-1",
    file_name: str = "Report.md",
) -> ParentChunkModel:
    return ParentChunkModel(
        parent_chunk_id=parent_id,
        content=content,
        file_metadata={"file_name": file_name, "file_id": file_id},
        parent_chunk_metadata={
            "child_chunks_ids": [child_id],
            "parent_chunk_number": chunk_number,
            "page_number": [0],
            "ingested_at": "2026-01-01T00:00:00+00:00",
        },
        content_flags={"is_image": False, "is_table_image": False},
        artifact_refs={"image_uuid": [], "table_image_uuid": []},
    )


def _make_child_model(
    *,
    child_id: str,
    parent_id: str,
    content: str,
    child_chunk_number: int,
    file_id: str = "file-1",
    file_name: str = "Report.md",
) -> ChildChunkModel:
    return ChildChunkModel(
        child_chunk_id=child_id,
        content=content,
        file_metadata={"file_name": file_name, "file_id": file_id},
        child_chunk_metadata={
            "parent_id": parent_id,
            "child_chunk_number": child_chunk_number,
            "page_number": 0,
            "has_preamble": False,
            "ingested_at": "2026-01-01T00:00:00+00:00",
        },
        content_flags={"is_image": False, "is_table_image": False},
        artifact_refs={"image_uuid": None, "table_image_uuid": None},
    )


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
        raise AssertionError("split_parent_child_chunks_from_markdown should not be called for no-op update")

    monkeypatch.setattr(rs, "delete_children_by_parent_id", _unexpected_delete_children)
    monkeypatch.setattr(rs, "delete_parent_document", _unexpected_delete_parent)
    monkeypatch.setattr(rs, "split_parent_child_chunks_from_markdown", _unexpected_split)

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


def test_reconstruction_service_update_file_rechunks_with_markdown_splitter(monkeypatch):
    class _FakeCollection:
        def find(self, _query):
            return iter(
                [
                    {
                        "_id": "parent-1",
                        "value": {
                            "page_content": "old content",
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
                    }
                ]
            )

    calls = {
        "split": [],
        "delete_children": [],
        "delete_parent": [],
        "polish": [],
        "upsert": [],
    }

    monkeypatch.setattr(rs, "PARENT_STORE", types.SimpleNamespace(collection=_FakeCollection()))

    async def _delete_children(parent_id: str):
        calls["delete_children"].append(parent_id)

    async def _delete_parent(parent_id: str):
        calls["delete_parent"].append(parent_id)

    def _split_markdown(new_content: str, file_name: str, **kwargs):
        calls["split"].append(
            {
                "content": new_content,
                "file_name": file_name,
                "kwargs": kwargs,
            }
        )
        parent_model = ParentChunkModel(
            parent_chunk_id="parent-new",
            content="## New heading\n\nUpdated body",
            file_metadata={"file_name": file_name, "file_id": kwargs["file_id"]},
            parent_chunk_metadata={
                "child_chunks_ids": ["child-new"],
                "parent_chunk_number": 0,
                "page_number": [0],
                "ingested_at": "2026-01-01T00:00:00+00:00",
            },
            content_flags={"is_image": False, "is_table_image": False},
            artifact_refs={"image_uuid": [], "table_image_uuid": []},
        )
        child_model = ChildChunkModel(
            child_chunk_id="child-new",
            content="Updated body",
            file_metadata={"file_name": file_name, "file_id": kwargs["file_id"]},
            child_chunk_metadata={
                "parent_id": "parent-new",
                "child_chunk_number": 0,
                "page_number": 0,
                "has_preamble": False,
                "ingested_at": "2026-01-01T00:00:00+00:00",
            },
            content_flags={"is_image": False, "is_table_image": False},
            artifact_refs={"image_uuid": None, "table_image_uuid": None},
        )
        return [parent_model], [child_model]

    def _polish_chunks(child_chunks):
        calls["polish"].append(child_chunks)
        return child_chunks

    async def _upsert_documents(*, parent_chunks, child_chunks):
        calls["upsert"].append({"parents": parent_chunks, "children": child_chunks})

    monkeypatch.setattr(rs, "split_parent_child_chunks_from_markdown", _split_markdown)
    monkeypatch.setattr(rs, "delete_children_by_parent_id", _delete_children)
    monkeypatch.setattr(rs, "delete_parent_document", _delete_parent)
    monkeypatch.setattr(rs, "polish_chunks", _polish_chunks)
    monkeypatch.setattr(rs, "upsert_documents", _upsert_documents)

    result = asyncio.run(
        rs.ReconstructionService.update_file(
            file_id="file-1",
            new_content="## New heading\n\nUpdated body",
            file_name="Report.md",
        )
    )

    assert calls["split"] == [
        {
            "content": "## New heading\n\nUpdated body",
            "file_name": "Report.md",
            "kwargs": {
                "file_id": "file-1",
                "parent_max_words": 500,
                "child_max_words": 80,
                "min_child_words": 20,
            },
        }
    ]
    assert calls["delete_children"] == ["parent-1"]
    assert calls["delete_parent"] == ["parent-1"]
    assert len(calls["polish"]) == 1
    assert len(calls["upsert"]) == 1
    assert calls["upsert"][0]["parents"][0]["parent_chunk_id"] == "parent-new"
    assert calls["upsert"][0]["parents"][0]["file_metadata"]["file_id"] == "file-1"
    assert calls["upsert"][0]["children"][0]["file_metadata"]["file_id"] == "file-1"
    assert result["fileId"] == "file-1"
    assert result["parentChunks"] == 1
    assert result["chunks"] == 1


def test_reconstruction_service_update_file_noop_when_only_markdown_spacing_differs(monkeypatch):
    class _FakeCollection:
        def find(self, _query):
            return iter(
                [
                    {
                        "_id": "parent-1",
                        "value": {
                            "page_content": "## Heading\n\n1. First item",
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
                    }
                ]
            )

    monkeypatch.setattr(rs, "PARENT_STORE", types.SimpleNamespace(collection=_FakeCollection()))

    async def _unexpected_delete_children(_parent_id: str):
        raise AssertionError("delete_children_by_parent_id should not be called for normalized no-op update")

    async def _unexpected_delete_parent(_parent_id: str):
        raise AssertionError("delete_parent_document should not be called for normalized no-op update")

    def _unexpected_split(*args, **kwargs):
        raise AssertionError("split_parent_child_chunks_from_markdown should not be called for normalized no-op update")

    monkeypatch.setattr(rs, "delete_children_by_parent_id", _unexpected_delete_children)
    monkeypatch.setattr(rs, "delete_parent_document", _unexpected_delete_parent)
    monkeypatch.setattr(rs, "split_parent_child_chunks_from_markdown", _unexpected_split)

    result = asyncio.run(
        rs.ReconstructionService.update_file(
            file_id="file-1",
            new_content="##   Heading\n\n1.  First item",
            file_name="Report.md",
        )
    )

    assert result["fileId"] == "file-1"
    assert result["content"] == "## Heading\n\n1. First item"
    assert result["chunks"] == 0


def test_reconstruction_service_update_file_rechunks_when_only_edge_whitespace_changes(monkeypatch):
    class _FakeCollection:
        def find(self, _query):
            return iter(
                [
                    {
                        "_id": "parent-1",
                        "value": {
                            "page_content": "original content",
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
                    }
                ]
            )

    calls = {
        "split": [],
        "delete_children": [],
        "delete_parent": [],
        "upsert": [],
    }

    monkeypatch.setattr(rs, "PARENT_STORE", types.SimpleNamespace(collection=_FakeCollection()))

    async def _delete_children(parent_id: str):
        calls["delete_children"].append(parent_id)

    async def _delete_parent(parent_id: str):
        calls["delete_parent"].append(parent_id)

    def _split_markdown(new_content: str, file_name: str, **kwargs):
        calls["split"].append(new_content)
        parent_model = ParentChunkModel(
            parent_chunk_id="parent-new",
            content=new_content,
            file_metadata={"file_name": file_name, "file_id": kwargs["file_id"]},
            parent_chunk_metadata={
                "child_chunks_ids": ["child-new"],
                "parent_chunk_number": 0,
                "page_number": [0],
                "ingested_at": "2026-01-01T00:00:00+00:00",
            },
            content_flags={"is_image": False, "is_table_image": False},
            artifact_refs={"image_uuid": [], "table_image_uuid": []},
        )
        child_model = ChildChunkModel(
            child_chunk_id="child-new",
            content=new_content,
            file_metadata={"file_name": file_name, "file_id": kwargs["file_id"]},
            child_chunk_metadata={
                "parent_id": "parent-new",
                "child_chunk_number": 0,
                "page_number": 0,
                "has_preamble": False,
                "ingested_at": "2026-01-01T00:00:00+00:00",
            },
            content_flags={"is_image": False, "is_table_image": False},
            artifact_refs={"image_uuid": None, "table_image_uuid": None},
        )
        return [parent_model], [child_model]

    async def _upsert_documents(*, parent_chunks, child_chunks):
        calls["upsert"].append({"parents": parent_chunks, "children": child_chunks})

    monkeypatch.setattr(rs, "split_parent_child_chunks_from_markdown", _split_markdown)
    monkeypatch.setattr(rs, "delete_children_by_parent_id", _delete_children)
    monkeypatch.setattr(rs, "delete_parent_document", _delete_parent)
    monkeypatch.setattr(rs, "polish_chunks", lambda child_chunks: child_chunks)
    monkeypatch.setattr(rs, "upsert_documents", _upsert_documents)

    result = asyncio.run(
        rs.ReconstructionService.update_file(
            file_id="file-1",
            new_content=" original content",
            file_name="Report.md",
        )
    )

    assert calls["split"] == [" original content"]
    assert calls["delete_children"] == ["parent-1"]
    assert calls["delete_parent"] == ["parent-1"]
    assert len(calls["upsert"]) == 1
    assert result["content"] == " original content"


def test_reconstruction_service_update_file_normalizes_markdown_and_preserves_hardbreaks(monkeypatch):
    class _FakeCollection:
        def find(self, _query):
            return iter(
                [
                    {
                        "_id": "parent-1",
                        "value": {
                            "page_content": "old content",
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
                    }
                ]
            )

    calls = {"split": []}

    monkeypatch.setattr(rs, "PARENT_STORE", types.SimpleNamespace(collection=_FakeCollection()))
    monkeypatch.setattr(rs, "delete_children_by_parent_id", lambda _parent_id: asyncio.sleep(0))
    monkeypatch.setattr(rs, "delete_parent_document", lambda _parent_id: asyncio.sleep(0))
    monkeypatch.setattr(rs, "polish_chunks", lambda child_chunks: child_chunks)
    monkeypatch.setattr(rs, "upsert_documents", lambda **kwargs: asyncio.sleep(0))

    def _split_markdown(new_content: str, file_name: str, **kwargs):
        calls["split"].append(new_content)
        parent_model = ParentChunkModel(
            parent_chunk_id="parent-new",
            content=new_content,
            file_metadata={"file_name": file_name, "file_id": kwargs["file_id"]},
            parent_chunk_metadata={
                "child_chunks_ids": ["child-new"],
                "parent_chunk_number": 0,
                "page_number": [0],
                "ingested_at": "2026-01-01T00:00:00+00:00",
            },
            content_flags={"is_image": False, "is_table_image": False},
            artifact_refs={"image_uuid": [], "table_image_uuid": []},
        )
        child_model = ChildChunkModel(
            child_chunk_id="child-new",
            content=new_content,
            file_metadata={"file_name": file_name, "file_id": kwargs["file_id"]},
            child_chunk_metadata={
                "parent_id": "parent-new",
                "child_chunk_number": 0,
                "page_number": 0,
                "has_preamble": False,
                "ingested_at": "2026-01-01T00:00:00+00:00",
            },
            content_flags={"is_image": False, "is_table_image": False},
            artifact_refs={"image_uuid": None, "table_image_uuid": None},
        )
        return [parent_model], [child_model]

    monkeypatch.setattr(rs, "split_parent_child_chunks_from_markdown", _split_markdown)

    result = asyncio.run(
        rs.ReconstructionService.update_file(
            file_id="file-1",
            new_content="##   Heading\n\nline 1  \nline 2\n\n* bullet",
            file_name="Report.md",
        )
    )

    assert calls["split"] == ["## Heading\n\nline 1  \nline 2\n\n- bullet"]
    assert result["content"] == "## Heading\n\nline 1  \nline 2\n\n- bullet"


def test_reconstruction_service_update_document_rechunks_with_markdown_splitter(monkeypatch):
    calls = {
        "split": [],
        "delete_children": [],
        "delete_parent": [],
        "polish": [],
        "upsert": [],
    }

    async def _aget(_parent_id: str):
        return {
            "metadata": {
                "file_metadata": {
                    "file_id": "file-7",
                }
            }
        }

    async def _delete_children(parent_id: str):
        calls["delete_children"].append(parent_id)

    async def _delete_parent(parent_id: str):
        calls["delete_parent"].append(parent_id)

    def _split_markdown(new_content: str, file_name: str, **kwargs):
        calls["split"].append(
            {
                "content": new_content,
                "file_name": file_name,
                "kwargs": kwargs,
            }
        )
        parent_model = ParentChunkModel(
            parent_chunk_id="parent-7-new",
            content=new_content,
            file_metadata={"file_name": file_name, "file_id": kwargs["file_id"]},
            parent_chunk_metadata={
                "child_chunks_ids": ["child-7-new"],
                "parent_chunk_number": 0,
                "page_number": [0],
                "ingested_at": "2026-01-01T00:00:00+00:00",
            },
            content_flags={"is_image": False, "is_table_image": False},
            artifact_refs={"image_uuid": [], "table_image_uuid": []},
        )
        child_model = ChildChunkModel(
            child_chunk_id="child-7-new",
            content="child::updated",
            file_metadata={"file_name": file_name, "file_id": kwargs["file_id"]},
            child_chunk_metadata={
                "parent_id": "parent-7-new",
                "child_chunk_number": 0,
                "page_number": 0,
                "has_preamble": False,
                "ingested_at": "2026-01-01T00:00:00+00:00",
            },
            content_flags={"is_image": False, "is_table_image": False},
            artifact_refs={"image_uuid": None, "table_image_uuid": None},
        )
        return [parent_model], [child_model]

    def _polish_chunks(child_chunks):
        calls["polish"].append(child_chunks)
        return child_chunks

    async def _upsert_documents(*, parent_chunks, child_chunks):
        calls["upsert"].append({"parents": parent_chunks, "children": child_chunks})

    monkeypatch.setattr(rs, "PARENT_STORE", types.SimpleNamespace(aget=_aget))
    monkeypatch.setattr(rs, "split_parent_child_chunks_from_markdown", _split_markdown)
    monkeypatch.setattr(rs, "delete_children_by_parent_id", _delete_children)
    monkeypatch.setattr(rs, "delete_parent_document", _delete_parent)
    monkeypatch.setattr(rs, "polish_chunks", _polish_chunks)
    monkeypatch.setattr(rs, "upsert_documents", _upsert_documents)

    result = asyncio.run(
        rs.ReconstructionService.update_document(
            parent_id="parent-7",
            new_content="## Updated\n\nBody",
            file_name="Report.md",
        )
    )

    assert calls["split"] == [
        {
            "content": "## Updated\n\nBody",
            "file_name": "Report.md",
            "kwargs": {
                "file_id": "file-7",
                "parent_max_words": 500,
                "child_max_words": 80,
                "min_child_words": 20,
            },
        }
    ]
    assert calls["delete_children"] == ["parent-7"]
    assert calls["delete_parent"] == ["parent-7"]
    assert len(calls["polish"]) == 1
    assert len(calls["upsert"]) == 1
    assert calls["upsert"][0]["parents"][0]["file_metadata"]["file_id"] == "file-7"
    assert calls["upsert"][0]["children"][0]["file_metadata"]["file_id"] == "file-7"
    assert result["previousParentId"] == "parent-7"
    assert result["parentId"] == "parent-7-new"
    assert result["chunks"] == 1


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
    assert result["requiresReload"] is False


def test_reconstruction_service_update_parent_chunks_batch_boundary_noop_when_content_unchanged(monkeypatch):
    class _FakeCollection:
        def find(self, _query):
            return iter(
                [
                    _make_parent_row(parent_id="parent-1", content="chunk one", chunk_number=0),
                    _make_parent_row(parent_id="parent-2", content="chunk two", chunk_number=1),
                ]
            )

    class _FakeParentStore:
        def __init__(self):
            self.collection = _FakeCollection()

        async def amset(self, _pairs):
            raise AssertionError("amset should not be called for unchanged boundary_rechunk")

    monkeypatch.setattr(rs, "PARENT_STORE", _FakeParentStore())

    async def _unexpected_delete_children(_parent_id: str):
        raise AssertionError("delete_children_by_parent_id should not be called for unchanged boundary_rechunk")

    async def _unexpected_delete_parent(_parent_id: str):
        raise AssertionError("delete_parent_document should not be called for unchanged boundary_rechunk")

    async def _unexpected_upsert(*, parent_chunks, child_chunks):
        raise AssertionError("upsert_documents should not be called for unchanged boundary_rechunk")

    def _unexpected_split(*args, **kwargs):
        raise AssertionError("split_parent_child_chunks_from_markdown should not be called for unchanged boundary_rechunk")

    monkeypatch.setattr(rs, "delete_children_by_parent_id", _unexpected_delete_children)
    monkeypatch.setattr(rs, "delete_parent_document", _unexpected_delete_parent)
    monkeypatch.setattr(rs, "upsert_documents", _unexpected_upsert)
    monkeypatch.setattr(rs, "split_parent_child_chunks_from_markdown", _unexpected_split)

    result = asyncio.run(
        rs.ReconstructionService.update_parent_chunks_batch(
            file_id="file-1",
            file_name="Report.md",
            updates=[],
            mode="boundary_rechunk",
            full_content="chunk one\n\nchunk two",
            touched_parent_ids=["parent-1", "parent-2"],
        )
    )

    assert result["updatedCount"] == 0
    assert result["results"] == []
    assert result["requiresReload"] is True


def test_reconstruction_service_update_parent_chunks_batch_boundary_rechunks_only_changed(monkeypatch):
    class _FakeCollection:
        def find(self, _query):
            return iter(
                [
                    _make_parent_row(parent_id="parent-1", content="chunk one", chunk_number=0),
                    _make_parent_row(parent_id="parent-2", content="chunk two", chunk_number=1),
                    _make_parent_row(parent_id="parent-3", content="chunk three", chunk_number=2),
                ]
            )

    calls = {
        "delete_children": [],
        "delete_parent": [],
        "upsert": [],
        "amset": [],
    }

    class _FakeParentStore:
        def __init__(self):
            self.collection = _FakeCollection()

        async def amset(self, pairs):
            calls["amset"].append(list(pairs))

    def _split_markdown(new_content: str, file_name: str, **kwargs):
        assert new_content == "chunk one updated\n\nchunk two\n\nchunk three"
        p1 = _make_parent_model(
            parent_id="new-parent-1",
            content="chunk one updated",
            chunk_number=0,
            child_id="new-child-1",
        )
        p2 = _make_parent_model(
            parent_id="new-parent-2",
            content="chunk two",
            chunk_number=1,
            child_id="new-child-2",
        )
        p3 = _make_parent_model(
            parent_id="new-parent-3",
            content="chunk three",
            chunk_number=2,
            child_id="new-child-3",
        )
        c1 = _make_child_model(
            child_id="new-child-1",
            parent_id="new-parent-1",
            content="child::chunk one updated",
            child_chunk_number=0,
        )
        c2 = _make_child_model(
            child_id="new-child-2",
            parent_id="new-parent-2",
            content="child::chunk two",
            child_chunk_number=1,
        )
        c3 = _make_child_model(
            child_id="new-child-3",
            parent_id="new-parent-3",
            content="child::chunk three",
            child_chunk_number=2,
        )
        return [p1, p2, p3], [c1, c2, c3]

    async def _delete_children(parent_id: str):
        calls["delete_children"].append(parent_id)

    async def _delete_parent(parent_id: str):
        calls["delete_parent"].append(parent_id)

    async def _upsert_documents(*, parent_chunks, child_chunks):
        calls["upsert"].append({"parents": parent_chunks, "children": child_chunks})

    def _polish_chunks(child_chunks):
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
            updates=[],
            mode="boundary_rechunk",
            full_content="chunk one updated\n\nchunk two\n\nchunk three",
            touched_parent_ids=["parent-1", "parent-2"],
        )
    )

    assert calls["delete_children"] == ["parent-1"]
    assert calls["delete_parent"] == ["parent-1"]
    assert len(calls["upsert"]) == 1
    assert len(calls["upsert"][0]["parents"]) == 1
    assert calls["upsert"][0]["parents"][0]["parent_chunk_id"] == "new-parent-1"
    assert len(calls["amset"]) == 1
    assert sorted(parent_id for parent_id, _ in calls["amset"][0]) == ["parent-2", "parent-3"]
    assert result["requiresReload"] is True
    assert result["updatedCount"] == 1
    assert result["results"][0]["previousParentId"] == "parent-1"
    assert result["results"][0]["parentId"] == "new-parent-1"


def test_reconstruction_service_update_parent_chunks_batch_boundary_end_append_updates_trailing(monkeypatch):
    class _FakeCollection:
        def find(self, _query):
            return iter(
                [
                    _make_parent_row(parent_id="parent-1", content="chunk one", chunk_number=0),
                    _make_parent_row(parent_id="parent-2", content="chunk two", chunk_number=1),
                ]
            )

    calls = {"delete_parent": []}

    class _FakeParentStore:
        def __init__(self):
            self.collection = _FakeCollection()

        async def amset(self, _pairs):
            return None

    def _split_markdown(new_content: str, file_name: str, **kwargs):
        p1 = _make_parent_model(
            parent_id="new-parent-1",
            content="chunk one",
            chunk_number=0,
            child_id="new-child-1",
        )
        p2 = _make_parent_model(
            parent_id="new-parent-2",
            content="chunk two appended",
            chunk_number=1,
            child_id="new-child-2",
        )
        c1 = _make_child_model(
            child_id="new-child-1",
            parent_id="new-parent-1",
            content="child::chunk one",
            child_chunk_number=0,
        )
        c2 = _make_child_model(
            child_id="new-child-2",
            parent_id="new-parent-2",
            content="child::chunk two appended",
            child_chunk_number=1,
        )
        return [p1, p2], [c1, c2]

    async def _delete_children(_parent_id: str):
        return None

    async def _delete_parent(parent_id: str):
        calls["delete_parent"].append(parent_id)

    async def _upsert_documents(*, parent_chunks, child_chunks):
        return None

    def _polish_chunks(child_chunks):
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
            updates=[],
            mode="boundary_rechunk",
            full_content="chunk one\n\nchunk two appended",
            touched_parent_ids=["parent-2"],
        )
    )

    assert calls["delete_parent"] == ["parent-2"]
    assert result["requiresReload"] is True
    assert result["updatedCount"] == 1


def test_reconstruction_service_update_parent_chunks_batch_boundary_duplicate_content_stable_diff(monkeypatch):
    class _FakeCollection:
        def find(self, _query):
            return iter(
                [
                    _make_parent_row(parent_id="parent-1", content="duplicate", chunk_number=0),
                    _make_parent_row(parent_id="parent-2", content="middle", chunk_number=1),
                    _make_parent_row(parent_id="parent-3", content="duplicate", chunk_number=2),
                ]
            )

    calls = {"delete_parent": []}

    class _FakeParentStore:
        def __init__(self):
            self.collection = _FakeCollection()

        async def amset(self, _pairs):
            return None

    def _split_markdown(new_content: str, file_name: str, **kwargs):
        p1 = _make_parent_model(
            parent_id="new-parent-1",
            content="duplicate",
            chunk_number=0,
            child_id="new-child-1",
        )
        p2 = _make_parent_model(
            parent_id="new-parent-2",
            content="middle updated",
            chunk_number=1,
            child_id="new-child-2",
        )
        p3 = _make_parent_model(
            parent_id="new-parent-3",
            content="duplicate",
            chunk_number=2,
            child_id="new-child-3",
        )
        c1 = _make_child_model(
            child_id="new-child-1",
            parent_id="new-parent-1",
            content="child::duplicate",
            child_chunk_number=0,
        )
        c2 = _make_child_model(
            child_id="new-child-2",
            parent_id="new-parent-2",
            content="child::middle updated",
            child_chunk_number=1,
        )
        c3 = _make_child_model(
            child_id="new-child-3",
            parent_id="new-parent-3",
            content="child::duplicate",
            child_chunk_number=2,
        )
        return [p1, p2, p3], [c1, c2, c3]

    async def _delete_children(_parent_id: str):
        return None

    async def _delete_parent(parent_id: str):
        calls["delete_parent"].append(parent_id)

    async def _upsert_documents(*, parent_chunks, child_chunks):
        return None

    def _polish_chunks(child_chunks):
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
            updates=[],
            mode="boundary_rechunk",
            full_content="duplicate\n\nmiddle updated\n\nduplicate",
            touched_parent_ids=["parent-1", "parent-2"],
        )
    )

    assert calls["delete_parent"] == ["parent-2"]
    assert result["requiresReload"] is True
    assert result["updatedCount"] == 1
