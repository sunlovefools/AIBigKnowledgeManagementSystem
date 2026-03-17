import asyncio
import base64
import importlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

stub_vectordb = types.ModuleType("app.vectordb.vectordb")


async def _unused_upsert_documents(**kwargs):
    return None


stub_vectordb.upsert_documents = _unused_upsert_documents
sys.modules["app.vectordb.vectordb"] = stub_vectordb

router_ingest = importlib.import_module("app.api.router_ingest")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


def test_ingest_upload_canonicalizes_legacy_chunks_before_upsert(monkeypatch):
    captured: dict[str, object] = {}
    raw_parent_content = "## Section 1: Nature &amp; Environment  \n\n1.  First item\n\n* bullet"
    raw_child_content = "## Section 1: Nature &amp; Environment  \n\n1.  First item"
    expected_parent_content = "## Section 1: Nature & Environment\n\n1. First item\n\n- bullet"
    expected_child_content = "## Section 1: Nature & Environment\n\n1. First item"

    monkeypatch.setattr(router_ingest, "_is_docling_pdf_strategy", lambda file: False)
    monkeypatch.setattr(
        router_ingest,
        "_run_legacy_pipeline",
        lambda file, file_bytes: (
            [
                {
                    "parent_chunk_id": "parent-1",
                    "content": raw_parent_content,
                    "file_metadata": {"file_name": file.fileName, "file_id": "file-1"},
                    "parent_chunk_metadata": {"parent_chunk_number": 0, "page_number": [0]},
                    "content_flags": {"is_image": False, "is_table_image": False},
                    "artifact_refs": {"image_uuid": [], "table_image_uuid": []},
                }
            ],
            [
                {
                    "child_chunk_id": "child-1",
                    "content": raw_child_content,
                    "file_metadata": {"file_name": file.fileName, "file_id": "file-1"},
                    "child_chunk_metadata": {
                        "parent_id": "parent-1",
                        "child_chunk_number": 0,
                        "page_number": 0,
                    },
                    "content_flags": {"is_image": False, "is_table_image": False},
                    "artifact_refs": {"image_uuid": None, "table_image_uuid": None},
                }
            ],
        ),
    )

    async def _capture_upsert(parent_chunks, child_chunks):
        captured["parent_chunks"] = parent_chunks
        captured["child_chunks"] = child_chunks

    monkeypatch.setattr(router_ingest, "_upsert_chunks", _capture_upsert)

    file_upload = router_ingest.FileUpload(
        fileName="sample.md",
        contentType="text/markdown",
        data=_b64(b"# raw markdown"),
    )

    response = asyncio.run(router_ingest.ingest_upload(file_upload))

    assert response.status == "ok"
    parent_chunks = captured["parent_chunks"]
    child_chunks = captured["child_chunks"]
    assert isinstance(parent_chunks, list)
    assert isinstance(child_chunks, list)
    assert parent_chunks[0]["content"] == expected_parent_content
    assert child_chunks[0]["content"] == expected_child_content
    assert parent_chunks[0]["file_metadata"] == {"file_name": "sample.md", "file_id": "file-1"}
    assert child_chunks[0]["file_metadata"] == {"file_name": "sample.md", "file_id": "file-1"}


def test_ingest_upload_canonicalizes_docling_chunks_before_upsert(monkeypatch):
    captured: dict[str, object] = {}
    raw_parent_content = "## Heading &amp; Detail\n\n2.  Second item"
    raw_child_content = "* bullet"
    expected_parent_content = "## Heading & Detail\n\n2. Second item"
    expected_child_content = "- bullet"

    monkeypatch.setattr(router_ingest, "_is_docling_pdf_strategy", lambda file: True)
    monkeypatch.setattr(
        router_ingest,
        "_run_docling_pipeline",
        lambda file, file_bytes: (
            [
                {
                    "parent_chunk_id": "parent-1",
                    "content": raw_parent_content,
                    "file_metadata": {"file_name": file.fileName, "file_id": "file-1"},
                    "parent_chunk_metadata": {"parent_chunk_number": 0, "page_number": [1]},
                    "content_flags": {"is_image": False, "is_table_image": False},
                    "artifact_refs": {"image_uuid": [], "table_image_uuid": []},
                }
            ],
            [
                {
                    "child_chunk_id": "child-1",
                    "content": raw_child_content,
                    "file_metadata": {"file_name": file.fileName, "file_id": "file-1"},
                    "child_chunk_metadata": {
                        "parent_id": "parent-1",
                        "child_chunk_number": 0,
                        "page_number": 1,
                    },
                    "content_flags": {"is_image": False, "is_table_image": False},
                    "artifact_refs": {"image_uuid": None, "table_image_uuid": None},
                }
            ],
            ["warning-1"],
            "run-1",
        ),
    )

    async def _capture_upsert(parent_chunks, child_chunks):
        captured["parent_chunks"] = parent_chunks
        captured["child_chunks"] = child_chunks

    monkeypatch.setattr(router_ingest, "_upsert_chunks", _capture_upsert)

    file_upload = router_ingest.FileUpload(
        fileName="sample.pdf",
        contentType="application/pdf",
        data=_b64(b"%PDF-1.4 fake"),
    )

    response = asyncio.run(router_ingest.ingest_upload(file_upload))

    assert response.status == "ok"
    parent_chunks = captured["parent_chunks"]
    child_chunks = captured["child_chunks"]
    assert isinstance(parent_chunks, list)
    assert isinstance(child_chunks, list)
    assert parent_chunks[0]["content"] == expected_parent_content
    assert child_chunks[0]["content"] == expected_child_content
    assert parent_chunks[0]["file_metadata"] == {"file_name": "sample.pdf", "file_id": "file-1"}
    assert child_chunks[0]["file_metadata"] == {"file_name": "sample.pdf", "file_id": "file-1"}
