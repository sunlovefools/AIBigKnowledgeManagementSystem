"""
In-memory background jobs for document ingestion uploads.

This mirrors the save-job service: the API accepts an upload quickly, while
heavy parsing/chunking runs outside the request lifecycle and off the event loop.
"""

from __future__ import annotations

import asyncio
import os
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from app.service.collection.collection_service import CollectionService
from app.service.rag.ingestion import ingest_upload_service

IngestJobStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]


class IngestJobValidationError(ValueError):
    """Raised when an ingest job payload is invalid."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_max_workers() -> int:
    raw = os.getenv("INGEST_JOB_MAX_WORKERS", "1")
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = 1
    return max(1, parsed)


@dataclass
class IngestJobRecord:
    job_id: str
    user_id: str
    file_name: str
    content_type: str
    data: str | None
    collection_id: str
    collection_name: str
    status: IngestJobStatus = "queued"
    result: dict[str, Any] | None = None
    error: str | None = None
    submitted_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "jobId": self.job_id,
            "status": self.status,
            "fileName": self.file_name,
            "collectionId": self.collection_id,
            "collectionName": self.collection_name,
            "result": self.result,
            "error": self.error,
            "submittedAt": self.submitted_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
        }


class IngestJobService:
    """Small process-local ingest-job registry."""

    _jobs: dict[str, IngestJobRecord] = {}
    _tasks: dict[str, asyncio.Task] = {}
    _lock = asyncio.Lock()
    _worker_semaphore = asyncio.Semaphore(_load_max_workers())

    @classmethod
    async def submit_ingest_job(
        cls,
        *,
        user_id: str,
        file_name: str,
        content_type: str,
        data: str,
        collection_id: str,
        collection_name: str,
    ) -> dict[str, Any]:
        normalized_user_id = str(user_id or "").strip()
        normalized_file_name = str(file_name or "").strip()
        normalized_content_type = str(content_type or "").strip()
        normalized_data = str(data or "")
        normalized_collection_id = str(collection_id or "").strip()
        normalized_collection_name = str(collection_name or CollectionService.DEFAULT_COLLECTION_NAME).strip()

        if not normalized_user_id:
            raise IngestJobValidationError("user_id must not be empty")
        if not normalized_file_name:
            raise IngestJobValidationError("fileName must not be empty")
        if not normalized_content_type:
            raise IngestJobValidationError("contentType must not be empty")
        if not normalized_data:
            raise IngestJobValidationError("data must not be empty")
        if not normalized_collection_id:
            raise IngestJobValidationError("collectionId must not be empty")

        job_id = uuid.uuid4().hex
        record = IngestJobRecord(
            job_id=job_id,
            user_id=normalized_user_id,
            file_name=normalized_file_name,
            content_type=normalized_content_type,
            data=normalized_data,
            collection_id=normalized_collection_id,
            collection_name=normalized_collection_name,
            submitted_at=_now_iso(),
        )

        async with cls._lock:
            cls._jobs[job_id] = record
            cls._tasks[job_id] = asyncio.create_task(cls._run_ingest_job(job_id))
            return record.public_dict()

    @classmethod
    async def get_ingest_job(cls, *, job_id: str, user_id: str) -> dict[str, Any] | None:
        normalized_job_id = str(job_id or "").strip()
        normalized_user_id = str(user_id or "").strip()
        async with cls._lock:
            record = cls._jobs.get(normalized_job_id)
            if not record or record.user_id != normalized_user_id:
                return None
            return record.public_dict()

    @classmethod
    async def cancel_ingest_job(cls, *, job_id: str, user_id: str) -> dict[str, Any] | None:
        normalized_job_id = str(job_id or "").strip()
        normalized_user_id = str(user_id or "").strip()
        async with cls._lock:
            record = cls._jobs.get(normalized_job_id)
            if not record or record.user_id != normalized_user_id:
                return None

            record.status = "canceled"
            record.finished_at = _now_iso()
            record.error = None
            record.data = None
            canceled_job = record.public_dict()

            cls._jobs.pop(normalized_job_id, None)
            task = cls._tasks.pop(normalized_job_id, None)

        if task and not task.done():
            task.cancel()

        return canceled_job

    @classmethod
    async def _set_status(
        cls,
        job_id: str,
        status_value: IngestJobStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        clear_payload: bool = False,
    ) -> None:
        async with cls._lock:
            record = cls._jobs.get(job_id)
            if not record:
                return
            record.status = status_value
            if status_value == "running":
                record.started_at = _now_iso()
            if status_value in ("succeeded", "failed", "canceled"):
                record.finished_at = _now_iso()
                record.result = result
                record.error = error
            if clear_payload:
                record.data = None

    @classmethod
    async def _get_record(cls, job_id: str) -> IngestJobRecord | None:
        async with cls._lock:
            return cls._jobs.get(job_id)

    @classmethod
    async def _run_ingest_job(cls, job_id: str) -> None:
        async with cls._worker_semaphore:
            await cls._set_status(job_id, "running")
            record = await cls._get_record(job_id)
            if not record:
                return

            try:
                if record.data is None:
                    raise IngestJobValidationError("Upload payload is no longer available.")

                service_result = await ingest_upload_service.run_ingest_upload(
                    file_name=record.file_name,
                    content_type=record.content_type,
                    data=record.data,
                )

                parent_chunks = service_result["parent_chunks"]
                child_chunks = service_result["child_chunks"]
                parent_chunks, child_chunks = CollectionService.apply_collection_metadata_to_chunks(
                    parent_chunks=parent_chunks,
                    child_chunks=child_chunks,
                    collection_id=record.collection_id,
                    collection_name=record.collection_name,
                )

                await ingest_upload_service.upsert_chunks(
                    parent_chunks=parent_chunks,
                    child_chunks=child_chunks,
                    user_id=record.user_id,
                )
                await CollectionService.reconcile_all_collection_file_counts(record.user_id)

                await cls._set_status(
                    job_id,
                    "succeeded",
                    result={
                        "status": "ok",
                        "message": "Upload completed successfully.",
                        "file_name": record.file_name,
                        "parent_chunks": len(parent_chunks),
                        "child_chunks": len(child_chunks),
                        "warnings": service_result["warnings"],
                    },
                    clear_payload=True,
                )
            except Exception as error:
                traceback.print_exc()
                await cls._set_status(
                    job_id,
                    "failed",
                    error=str(error),
                    clear_payload=True,
                )
            finally:
                async with cls._lock:
                    cls._tasks.pop(job_id, None)

    @classmethod
    async def reset_for_tests(cls) -> None:
        async with cls._lock:
            for task in cls._tasks.values():
                if not task.done():
                    task.cancel()
            cls._jobs.clear()
            cls._tasks.clear()
            cls._worker_semaphore = asyncio.Semaphore(_load_max_workers())
