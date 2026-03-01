import asyncio
import base64
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api import router_ingest
from app.service.rag.ingestion.docling.models import DoclingStructuredBlock
from app.service.rag.ingestion.docling_pdf_extractor import (
    DoclingChunkFailure,
    DoclingParseResult,
    DoclingParseStats,
)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


def _fake_docling_result(
    *,
    structured_blocks: list[DoclingStructuredBlock] | None = None,
) -> DoclingParseResult:
    return DoclingParseResult(
        source_file_name="sample.pdf",
        artifact_run_id="run-1",
        artifact_dir="backend/_local_uploads/docling_previews/run-1",
        markdown_path="backend/_local_uploads/docling_previews/run-1/document.md",
        markdown_text="hello markdown",
        images=[],
        warnings=["warning-1"],
        partial_failures=[DoclingChunkFailure(page_range="1-6", errors=["err"])],
        stats=DoclingParseStats(
            converted_chunks=1,
            partial_failure_chunks=1,
            pictures_extracted=0,
            table_fallback_images_extracted=0,
        ),
        structured_blocks=structured_blocks or [],
    )


def test_preview_endpoint_rejects_non_pdf():
    file_upload = router_ingest.FileUpload(
        fileName="notes.txt",
        contentType="text/plain",
        data=_b64(b"hello"),
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(router_ingest.ingest_webhook_preview(file_upload))
    assert exc.value.status_code == 415


def test_preview_endpoint_returns_manifest_summary(monkeypatch):
    monkeypatch.setattr(router_ingest, "parse_pdf_with_docling_preview", lambda **_: _fake_docling_result())

    file_upload = router_ingest.FileUpload(
        fileName="sample.pdf",
        contentType="application/pdf",
        data=_b64(b"%PDF-1.4 fake"),
    )
    response = asyncio.run(router_ingest.ingest_webhook_preview(file_upload))
    assert response.status == "ok"
    assert response.artifact_run_id == "run-1"
    assert response.stats["converted_chunks"] == 1
    assert response.partial_failures[0]["page_range"] == "1-6"


def test_webhook_uses_legacy_extractor_by_default(monkeypatch):
    captured = {}

    monkeypatch.setattr(router_ingest, "get_pdf_ingestion_strategy", lambda: "legacy")
    monkeypatch.setattr(router_ingest, "parse_pdf_with_docling_preview", lambda **_: (_ for _ in ()).throw(AssertionError("should not call docling")))
    monkeypatch.setattr(router_ingest, "extract_text", lambda content_type, data: "legacy-text")

    def _split(text, file_name, parent_target_chars, child_max_chars):
        captured["text"] = text
        captured["file_name"] = file_name
        return [], []

    async def _upsert_documents(**kwargs):
        captured["upsert"] = kwargs

    monkeypatch.setattr(router_ingest, "split_parent_child_chunks", _split)
    monkeypatch.setattr(router_ingest, "polish_chunks", lambda chunks: chunks)
    monkeypatch.setattr(router_ingest, "upsert_documents", _upsert_documents)

    file_upload = router_ingest.FileUpload(
        fileName="sample.pdf",
        contentType="application/pdf",
        data=_b64(b"%PDF-1.4 fake"),
    )
    asyncio.run(router_ingest.ingest_webhook(file_upload))
    assert captured["text"] == "legacy-text"


def test_webhook_uses_docling_markdown_when_enabled(monkeypatch):
    captured = {}

    monkeypatch.setattr(router_ingest, "get_pdf_ingestion_strategy", lambda: "docling")
    monkeypatch.setattr(router_ingest, "extract_text", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not call legacy extractor")))
    monkeypatch.setattr(router_ingest, "parse_pdf_with_docling_preview", lambda **_: _fake_docling_result())

    def _split(text, file_name, parent_target_chars, child_max_chars):
        captured["text"] = text
        captured["file_name"] = file_name
        return [], []

    async def _upsert_documents(**kwargs):
        captured["upsert"] = kwargs

    monkeypatch.setattr(router_ingest, "split_parent_child_chunks", _split)
    monkeypatch.setattr(router_ingest, "polish_chunks", lambda chunks: chunks)
    monkeypatch.setattr(router_ingest, "upsert_documents", _upsert_documents)

    file_upload = router_ingest.FileUpload(
        fileName="sample.pdf",
        contentType="application/pdf",
        data=_b64(b"%PDF-1.4 fake"),
    )
    asyncio.run(router_ingest.ingest_webhook(file_upload))
    assert captured["text"] == "hello markdown"


def test_webhook_non_pdf_stays_legacy_even_when_docling_enabled(monkeypatch):
    captured = {}

    monkeypatch.setattr(router_ingest, "get_pdf_ingestion_strategy", lambda: "docling")
    monkeypatch.setattr(router_ingest, "parse_pdf_with_docling_preview", lambda **_: (_ for _ in ()).throw(AssertionError("should not call docling for text/plain")))
    monkeypatch.setattr(router_ingest, "extract_text", lambda content_type, data: "plain-text")

    def _split(text, file_name, parent_target_chars, child_max_chars):
        captured["text"] = text
        return [], []

    async def _upsert_documents(**kwargs):
        captured["upsert"] = True

    monkeypatch.setattr(router_ingest, "split_parent_child_chunks", _split)
    monkeypatch.setattr(router_ingest, "polish_chunks", lambda chunks: chunks)
    monkeypatch.setattr(router_ingest, "upsert_documents", _upsert_documents)

    file_upload = router_ingest.FileUpload(
        fileName="sample.txt",
        contentType="text/plain",
        data=_b64(b"hello"),
    )
    asyncio.run(router_ingest.ingest_webhook(file_upload))
    assert captured["text"] == "plain-text"


def test_preview_uses_docling_block_chunker_when_structured_blocks_present(monkeypatch):
    captured = {}
    structured_blocks = [
        DoclingStructuredBlock(
            block_index=0,
            block_type="header",
            content="Section Header",
        ),
        DoclingStructuredBlock(
            block_index=1,
            block_type="text",
            content="Body content for chunking.",
        ),
    ]

    monkeypatch.setattr(
        router_ingest,
        "parse_pdf_with_docling_preview",
        lambda **_: _fake_docling_result(structured_blocks=structured_blocks),
    )

    async def _upsert_documents(**kwargs):
        captured["upsert_called"] = True
        captured["upsert_kwargs"] = kwargs

    def _split_docling_blocks(blocks, file_name, artifact_dir, file_id=None):
        captured["docling_blocks_called"] = True
        captured["block_count"] = len(blocks)
        captured["artifact_dir"] = artifact_dir
        captured["file_id"] = file_id
        return [], []

    monkeypatch.setattr(router_ingest, "split_parent_child_chunks_from_docling_blocks", _split_docling_blocks)
    monkeypatch.setattr(router_ingest, "upsert_documents", _upsert_documents)

    file_upload = router_ingest.FileUpload(
        fileName="sample.pdf",
        contentType="application/pdf",
        data=_b64(b"%PDF-1.4 fake"),
    )
    asyncio.run(router_ingest.ingest_webhook_preview(file_upload))
    assert captured["docling_blocks_called"] is True
    assert captured["block_count"] == 2
    assert captured["artifact_dir"].endswith("run-1")
    assert captured["upsert_called"] is True


def test_preview_skips_docling_block_chunker_when_structured_blocks_missing(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        router_ingest,
        "parse_pdf_with_docling_preview",
        lambda **_: _fake_docling_result(structured_blocks=[]),
    )

    monkeypatch.setattr(
        router_ingest,
        "split_parent_child_chunks_from_docling_blocks",
        lambda *args, **kwargs: captured.setdefault("docling_blocks_called", True),
    )

    file_upload = router_ingest.FileUpload(
        fileName="sample.pdf",
        contentType="application/pdf",
        data=_b64(b"%PDF-1.4 fake"),
    )
    response = asyncio.run(router_ingest.ingest_webhook_preview(file_upload))
    assert response.status == "ok"
    assert "docling_blocks_called" not in captured
