# Module purpose:
# Handles async table-image VLM job execution, context-aware submission timing, and
# final markdown placeholder replacement plus aggregate result artifact generation.

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .artifacts import _prepare_table_image_vlm_worker_paths
from .context import _is_text_like_context_block, _normalize_markdown_context_for_vlm
from .models import TableImageVlmJob, TableImageVlmRuntime, TableImageVlmWorkerResult


def _process_table_image_vlm_job(
    runtime: TableImageVlmRuntime,
    job: TableImageVlmJob,
    *,
    context_before: str,
    context_after: str,
) -> TableImageVlmWorkerResult:
    """
    Execute one table-image VLM job.

    Runs table JSON extraction and semantic summary generation concurrently, then merges
    both outcomes into a single worker result object.
    """

    helper = runtime.helper_module
    output_dir, json_path, summary_path = _prepare_table_image_vlm_worker_paths(job)

    result = TableImageVlmWorkerResult(
        json_ok=False,
        summary_ok=False,
        json_path=str(json_path),
        summary_path=str(summary_path),
    )

    # Nested helpers below are worker-local call wrappers.
    # They capture `job`, `runtime`, `output_dir`, and the prepared context strings so we can
    # submit JSON extraction and summary generation concurrently via the inner thread pool.
    def _run_json() -> Any:
        """
        Worker-local helper for the table-to-JSON VLM call.

        Used only inside `_process_table_image_vlm_job()` when submitting concurrent tasks.
        """
        print("Sending image to VLM for JSON extraction...\n")
        return helper.extract_table_json_from_image(
            image_path=job.image_artifact.file_path,
            api_key=runtime.api_key,
            output_dir=output_dir,
            save_artifacts=True,
        )

    def _run_summary() -> str:
        """
        Worker-local helper for the semantic-summary VLM call.

        Uses the context strings captured at job submission time (`context_before` /
        `context_after`) so the summary reflects surrounding document text.
        """
        print("Sending image + context to VLM for semantic summary generation...\n")
        return helper.extract_table_semantic_summary_from_image(
            image_path=job.image_artifact.file_path,
            context_before=context_before,
            context_after=context_after,
            api_key=runtime.api_key,
            output_dir=output_dir,
            save_artifacts=True,
        )

    # Two concurrent calls against the same VLM endpoint/model:
    # one for structured table JSON, one for contextual summary text.
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="table-vlm-call") as inner:
        json_future = inner.submit(_run_json)
        summary_future = inner.submit(_run_summary)

        try:
            _ = json_future.result()
            result.json_ok = True
        except Exception as exc:
            result.errors.append(f"JSON extraction failed: {exc}")

        try:
            summary_text = summary_future.result()
            summary_text = " ".join((summary_text or "").split()).strip()
            if summary_text:
                result.summary_text = summary_text
                result.summary_ok = True
        except Exception as exc:
            result.errors.append(f"Summary extraction failed: {exc}")

    return result


def submit_ready_table_image_vlm_jobs(
    *,
    runtime: TableImageVlmRuntime | None,
    executor: ThreadPoolExecutor | None,
    jobs: list[TableImageVlmJob],
    markdown_parts: list[str],
    warnings: list[str],
    force: bool = False,
) -> None:
    """
    Submit any queued jobs that have enough surrounding markdown context.

    By default, jobs wait until some "after" context exists. `force=True` submits all remaining
    jobs (used at end-of-document finalization).
    """

    if runtime is None or executor is None or not jobs:
        return

    for job in jobs:
        # Skip jobs that were already handed off to the thread pool.
        if job.submitted:
            continue

        # Use a fixed number of blocks around the table instead of a character window.
        before_blocks = markdown_parts[max(0, job.block_index - runtime.context_blocks) : job.block_index]
        raw_after_blocks = markdown_parts[job.block_index + 1 :]
        text_like_after_blocks = [
            block for block in raw_after_blocks if _is_text_like_context_block(block)
        ]
        after_blocks = text_like_after_blocks[: runtime.context_blocks]

        # Normal mode: wait until enough following text blocks exist so the summary sees the after context.
        if not force and len(text_like_after_blocks) < runtime.after_ready_blocks:
            continue

        # Finalization mode (`force=True`): allow before-only submission when the table is
        # effectively terminal (e.g., last page / end of document) and there is no text after it.
        if force and not text_like_after_blocks:
            after_blocks = []

        # Join selected blocks into strings and normalize before sending to the summary helper.
        context_before = _normalize_markdown_context_for_vlm("\n\n".join(before_blocks))
        context_after = _normalize_markdown_context_for_vlm("\n\n".join(after_blocks))
        job.context_before = context_before
        job.context_after = context_after

        try:
            # Submitting the job once able to get the before and after context
            job.future = executor.submit(
                _process_table_image_vlm_job,
                runtime,
                job,
                context_before=context_before,
                context_after=context_after,
            )
            job.submitted = True
        except Exception as exc:
            warnings.append(
                "Failed to submit table-image VLM job "
                f"image_uuid={job.image_artifact.image_uuid}: {exc}"
            )


def _build_table_image_vlm_summary_replacement(
    *,
    job: TableImageVlmJob,
    result: TableImageVlmWorkerResult | None,
) -> str:
    """Build the markdown replacement text for a table-image summary placeholder."""

    json_marker = f"<!-- table-image-vlm-json-path: {job.json_rel_path} -->"

    if result is not None and result.summary_ok and result.summary_text:
        return f"{json_marker}\n> **Table summary (VLM)**: {result.summary_text}"

    return (
        f"{json_marker}\n"
        "> **Table summary (VLM)**: Summary unavailable (see VLM artifacts/errors)."
    )


def finalize_table_image_vlm_jobs(
    *,
    artifact_dir: Path,
    jobs: list[TableImageVlmJob],
    markdown_parts: list[str],
    warnings: list[str],
) -> None:
    """
    Wait for submitted jobs, patch markdown placeholders, and persist aggregate debug results.

    This is called once per document near the end of parsing.
    """

    if not jobs:
        return

    aggregate_entries: list[dict[str, Any]] = []

    # Loop through all the jobs that were submitted to the thread pool
    for job in jobs:
        result: TableImageVlmWorkerResult | None = None
        if job.future is not None:
            try:
                # Collect worker result and store it on the job for later inspection/debugging.
                result = job.future.result()
                job.result = result
            except Exception as exc:
                result = TableImageVlmWorkerResult(
                    json_ok=False,
                    summary_ok=False,
                    errors=[f"Unhandled worker exception: {exc}"],
                )
                job.result = result

        if result is not None and result.errors:
            for error in result.errors:
                warnings.append(
                    "Table-image VLM issue "
                    f"image_uuid={job.image_artifact.image_uuid}: {error}"
                )

        # Replace the placeholder in the exact markdown block where this table image was emitted.
        replacement = _build_table_image_vlm_summary_replacement(job=job, result=result)
        block = markdown_parts[job.block_index]
        if job.summary_placeholder in block:
            markdown_parts[job.block_index] = block.replace(job.summary_placeholder, replacement)
        else:
            markdown_parts[job.block_index] = f"{block}\n{replacement}"

        aggregate_entries.append(
            {
                "image_uuid": job.image_artifact.image_uuid,
                "table_index": job.table_index,
                "page_no": job.page_no,
                "image_file_name": job.image_artifact.file_name,
                "image_file_path": job.image_artifact.file_path,
                "json_rel_path": job.json_rel_path,
                "json_path": None if result is None else result.json_path,
                "summary_path": None if result is None else result.summary_path,
                "json_ok": False if result is None else result.json_ok,
                "summary_ok": False if result is None else result.summary_ok,
                "summary_text": None if result is None else result.summary_text,
                "context_before_chars": len(job.context_before),
                "context_after_chars": len(job.context_after),
                "errors": [] if result is None else result.errors,
            }
        )

    # Single run-level summary file that points to each per-table debug artifact set.
    (artifact_dir / "table_image_vlm_results.json").write_text(
        json.dumps({"tables": aggregate_entries}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
