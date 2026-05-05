"""
In-memory background jobs for document modification saves.

The frontend treats a save job acceptance as the fast user-facing save event,
while this service continues the existing chunk update/re-ingestion work on a
background asyncio task.
"""

import asyncio
import hashlib
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from app.service.modification.reconstruction_service import ReconstructionService
from app.service.rag.ingestion.markdown_canonicalizer import (
    normalize_markdown_for_modification,
)

SaveJobStatus = Literal["queued", "running", "succeeded", "failed"]
SaveJobMode = Literal["fast_updates", "boundary_rechunk", "full_file"]


class SaveJobConflictError(RuntimeError):
    """Raised when a file already has an active save job."""


class SaveJobValidationError(ValueError):
    """Raised when a save job payload is invalid."""


@dataclass
class SaveJobRecord:
    job_id: str
    user_id: str
    file_id: str
    file_name: str
    content: str
    mode: SaveJobMode
    status: SaveJobStatus = "queued"
    updates: list[dict[str, str]] | None = None
    touched_parent_ids: list[str] | None = None
    new_file_name: str | None = None
    expected_content_hash: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    submitted_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "jobId": self.job_id,
            "status": self.status,
            "fileId": self.file_id,
            "result": self.result,
            "error": self.error,
            "submittedAt": self.submitted_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SaveJobService:
    """Small process-local save-job registry."""

    _jobs: dict[str, SaveJobRecord] = {}
    _active_by_file: dict[str, str] = {}
    _tasks: dict[str, asyncio.Task] = {}
    _lock = asyncio.Lock()

    @staticmethod
    def calculate_content_hash(content: str) -> str:
        normalized = normalize_markdown_for_modification(str(content or ""))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @classmethod
    def _active_key(cls, user_id: str, file_id: str) -> str:
        return f"{user_id}:{file_id}"

    @classmethod
    async def submit_save_job(
        cls,
        *,
        user_id: str,
        file_id: str,
        file_name: str,
        content: str,
        mode: SaveJobMode,
        updates: list[dict[str, str]] | None = None,
        touched_parent_ids: list[str] | None = None,
        new_file_name: str | None = None,
        expected_content_hash: str | None = None,
    ) -> dict[str, Any]:
        normalized_user_id = str(user_id or "").strip()
        normalized_file_id = str(file_id or "").strip()
        normalized_file_name = str(file_name or "").strip()
        normalized_content = normalize_markdown_for_modification(str(content or ""))
        normalized_new_file_name = str(new_file_name or "").strip() or None

        if not normalized_user_id:
            raise SaveJobValidationError("user_id must not be empty")
        if not normalized_file_id:
            raise SaveJobValidationError("fileId must not be empty")
        if not normalized_file_name:
            raise SaveJobValidationError("fileName must not be empty")
        if mode not in ("fast_updates", "boundary_rechunk", "full_file"):
            raise SaveJobValidationError(f"Unsupported mode='{mode}'")
        if not normalized_content.strip():
            raise SaveJobValidationError("content must not be empty")

        cleaned_updates: list[dict[str, str]] = []
        for item in updates or []:
            parent_id = str(item.get("parentId") or "").strip()
            item_content = normalize_markdown_for_modification(str(item.get("content") or ""))
            if parent_id:
                cleaned_updates.append({"parentId": parent_id, "content": item_content})

        cleaned_touched_parent_ids = [
            str(parent_id).strip()
            for parent_id in (touched_parent_ids or [])
            if str(parent_id).strip()
        ]

        if mode == "fast_updates" and not cleaned_updates:
            raise SaveJobValidationError("updates must contain at least one parent chunk update")
        if mode == "boundary_rechunk" and not cleaned_touched_parent_ids:
            raise SaveJobValidationError("touchedParentIds must contain at least one parent ID")

        active_key = cls._active_key(normalized_user_id, normalized_file_id)
        async with cls._lock:
            active_job_id = cls._active_by_file.get(active_key)
            if active_job_id:
                active_job = cls._jobs.get(active_job_id)
                if active_job and active_job.status in ("queued", "running"):
                    raise SaveJobConflictError("A save job is already active for this file.")

            job_id = uuid.uuid4().hex
            record = SaveJobRecord(
                job_id=job_id,
                user_id=normalized_user_id,
                file_id=normalized_file_id,
                file_name=normalized_file_name,
                content=normalized_content,
                mode=mode,
                updates=cleaned_updates,
                touched_parent_ids=cleaned_touched_parent_ids,
                new_file_name=normalized_new_file_name,
                expected_content_hash=str(expected_content_hash or "").strip().lower() or None,
                submitted_at=_now_iso(),
            )
            cls._jobs[job_id] = record
            cls._active_by_file[active_key] = job_id
            cls._tasks[job_id] = asyncio.create_task(cls._run_save_job(job_id))
            return record.public_dict()

    @classmethod
    async def get_save_job(cls, *, job_id: str, user_id: str) -> dict[str, Any] | None:
        normalized_job_id = str(job_id or "").strip()
        normalized_user_id = str(user_id or "").strip()
        async with cls._lock:
            record = cls._jobs.get(normalized_job_id)
            if not record or record.user_id != normalized_user_id:
                return None
            return record.public_dict()

    @classmethod
    async def _set_status(
        cls,
        job_id: str,
        status_value: SaveJobStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        async with cls._lock:
            record = cls._jobs.get(job_id)
            if not record:
                return
            record.status = status_value
            if status_value == "running":
                record.started_at = _now_iso()
            if status_value in ("succeeded", "failed"):
                record.finished_at = _now_iso()
                record.result = result
                record.error = error
                active_key = cls._active_key(record.user_id, record.file_id)
                if cls._active_by_file.get(active_key) == job_id:
                    del cls._active_by_file[active_key]

    @classmethod
    async def _get_record(cls, job_id: str) -> SaveJobRecord | None:
        async with cls._lock:
            return cls._jobs.get(job_id)

    @classmethod
    async def _run_save_job(cls, job_id: str) -> None:
        await cls._set_status(job_id, "running")
        record = await cls._get_record(job_id)
        if not record:
            return

        try:
            if record.expected_content_hash:
                current_content = await ReconstructionService.get_file_merged_content(
                    file_id=record.file_id,
                    file_name=record.file_name,
                    user_id=record.user_id,
                )
                current_hash = cls.calculate_content_hash(current_content)
                if current_hash != record.expected_content_hash:
                    raise SaveJobConflictError(
                        "The file changed after editing began. Reload the file and reapply your changes."
                    )

            result: dict[str, Any]
            if record.mode == "fast_updates":
                result = await ReconstructionService.update_parent_chunks_batch(
                    file_id=record.file_id,
                    file_name=record.file_name,
                    updates=record.updates or [],
                    user_id=record.user_id,
                    mode="fast_updates",
                )
            elif record.mode == "boundary_rechunk":
                result = await ReconstructionService.update_parent_chunks_batch(
                    file_id=record.file_id,
                    file_name=record.file_name,
                    updates=[],
                    user_id=record.user_id,
                    mode="boundary_rechunk",
                    full_content=record.content,
                    touched_parent_ids=record.touched_parent_ids or [],
                )
            elif record.mode == "full_file":
                result = await ReconstructionService.update_file(
                    file_id=record.file_id,
                    new_content=record.content,
                    file_name=record.file_name,
                    user_id=record.user_id,
                )
            else:
                raise SaveJobValidationError(f"Unsupported mode='{record.mode}'")

            if record.new_file_name and record.new_file_name != record.file_name:
                rename_result = await ReconstructionService.rename_file(
                    file_id=record.file_id,
                    new_file_name=record.new_file_name,
                    user_id=record.user_id,
                )
                result = {
                    **result,
                    "fileName": rename_result.get("fileName", record.new_file_name),
                    "renameResult": rename_result,
                }

            await cls._set_status(job_id, "succeeded", result=result)
        except Exception as error:
            traceback.print_exc()
            await cls._set_status(job_id, "failed", error=str(error))
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
            cls._active_by_file.clear()
            cls._tasks.clear()
