import asyncio
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

stub_vectordb = types.ModuleType("app.vectordb.vectordb")


async def _unused_upsert_documents(**kwargs):
    return None


stub_vectordb.upsert_documents = _unused_upsert_documents
sys.modules["app.vectordb.vectordb"] = stub_vectordb

from app.api import router_ingest
from app.service.rag.ingestion.ingest_job_service import IngestJobService


@pytest.fixture(autouse=True)
def _patch_collection_service(monkeypatch):
    async def _resolve_active_collection(*, user_id: str, requested_collection_id: str | None = None):
        _ = user_id
        return {
            "collection_id": requested_collection_id or "collection-default",
            "name": "Default",
        }

    async def _reconcile_all_collection_file_counts(user_id: str):
        _ = user_id
        return None

    monkeypatch.setattr(router_ingest.CollectionService, "resolve_active_collection", _resolve_active_collection)
    monkeypatch.setattr(
        router_ingest.CollectionService,
        "reconcile_all_collection_file_counts",
        _reconcile_all_collection_file_counts,
    )


async def _wait_for_terminal_job(job_id: str, user_id: str = "user-1") -> router_ingest.IngestJobStatusResponse:
    for _ in range(50):
        status_response = await router_ingest.get_ingest_upload_job_status(
            job_id,
            current_user={"sub": user_id},
        )
        if status_response.status in {"succeeded", "failed"}:
            return status_response
        await asyncio.sleep(0.01)
    raise AssertionError("Ingest job did not reach a terminal state.")


def test_ingest_upload_job_accepts_then_succeeds(monkeypatch):
    async def _scenario():
        await IngestJobService.reset_for_tests()
        captured = {}

        async def _fake_run_ingest_upload(*, file_name, content_type, data):
            captured["run"] = {
                "file_name": file_name,
                "content_type": content_type,
                "data": data,
            }
            await asyncio.sleep(0.01)
            return {
                "parent_chunks": [{"parent_chunk_id": "p1", "metadata": {}}],
                "child_chunks": [{"child_chunk_id": "c1", "metadata": {}}],
                "warnings": ["warning-1"],
            }

        async def _fake_upsert_chunks(*, parent_chunks, child_chunks, user_id):
            captured["upsert"] = {
                "parent_chunks": parent_chunks,
                "child_chunks": child_chunks,
                "user_id": user_id,
            }

        monkeypatch.setattr(router_ingest.ingest_upload_service, "run_ingest_upload", _fake_run_ingest_upload)
        monkeypatch.setattr(router_ingest.ingest_upload_service, "upsert_chunks", _fake_upsert_chunks)

        accepted = await router_ingest.create_ingest_upload_job(
            router_ingest.FileUpload(
                fileName="example.txt",
                contentType="text/plain",
                data="SGVsbG8=",
                collectionId="collection-1",
            ),
            current_user={"sub": "user-1"},
        )

        assert accepted.status == "queued"
        assert accepted.fileName == "example.txt"
        assert accepted.collectionId == "collection-1"

        final_status = await _wait_for_terminal_job(accepted.jobId)
        assert final_status.status == "succeeded"
        assert final_status.result is not None
        assert final_status.result.file_name == "example.txt"
        assert final_status.result.parent_chunks == 1
        assert final_status.result.child_chunks == 1
        assert final_status.result.warnings == ["warning-1"]
        assert captured["run"]["data"] == "SGVsbG8="
        assert captured["upsert"]["user_id"] == "user-1"
        assert captured["upsert"]["parent_chunks"][0]["collection_metadata"]["collection_id"] == "collection-1"

        stored = await IngestJobService._get_record(accepted.jobId)
        assert stored is not None
        assert stored.data is None

    asyncio.run(_scenario())


def test_ingest_upload_job_failure_is_reported(monkeypatch):
    async def _scenario():
        await IngestJobService.reset_for_tests()

        async def _fake_run_ingest_upload(*, file_name, content_type, data):
            _ = file_name, content_type, data
            raise router_ingest.ingest_upload_service.InvalidBase64PayloadError("invalid base64 payload")

        monkeypatch.setattr(router_ingest.ingest_upload_service, "run_ingest_upload", _fake_run_ingest_upload)

        accepted = await router_ingest.create_ingest_upload_job(
            router_ingest.FileUpload(
                fileName="bad.txt",
                contentType="text/plain",
                data="not-base64",
            ),
            current_user={"sub": "user-1"},
        )

        final_status = await _wait_for_terminal_job(accepted.jobId)
        assert final_status.status == "failed"
        assert final_status.error is not None
        assert "invalid base64 payload" in final_status.error

    asyncio.run(_scenario())


def test_ingest_upload_job_status_is_user_scoped(monkeypatch):
    async def _scenario():
        await IngestJobService.reset_for_tests()

        async def _fake_run_ingest_upload(*, file_name, content_type, data):
            _ = file_name, content_type, data
            return {
                "parent_chunks": [],
                "child_chunks": [],
                "warnings": [],
            }

        monkeypatch.setattr(router_ingest.ingest_upload_service, "run_ingest_upload", _fake_run_ingest_upload)

        accepted = await router_ingest.create_ingest_upload_job(
            router_ingest.FileUpload(
                fileName="example.txt",
                contentType="text/plain",
                data="SGVsbG8=",
            ),
            current_user={"sub": "user-1"},
        )

        with pytest.raises(HTTPException) as exc:
            await router_ingest.get_ingest_upload_job_status(
                accepted.jobId,
                current_user={"sub": "user-2"},
            )
        assert exc.value.status_code == 404

    asyncio.run(_scenario())
