"""
Lifecycle/finalization helpers for Docling layout processing.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.service.rag.ingestion.docling import table_image_vlm
from app.service.rag.ingestion.docling.models import ExtractedImageArtifact
from app.service.rag.ingestion.docling.utils import markdown_builder
from app.service.rag.ingestion.docling.utils.table_data_artifacts import (
    persist_table_data_toon_artifacts,
)


def finalize_table_image_jobs(
    *,
    artifact_dir: Path | None,
    table_image_vlm_runtime: table_image_vlm.TableImageVlmRuntime | None,
    table_image_vlm_executor: ThreadPoolExecutor | None,
    table_image_vlm_jobs: list[table_image_vlm.TableImageVlmJob],
    markdown_parts: list[str],
    warnings: list[str],
    resolved_file_id: str,
    file_name: str,
) -> None:
    """
    Flush and finalize queued table-image VLM jobs in a fixed order.

    Order matters:
    1) force-submit remaining ready jobs
    2) finalize placeholders/results into markdown
    3) persist table-data TOON artifacts
    4) shutdown worker pool
    """

    if table_image_vlm_executor is None:
        return

    table_image_vlm.submit_ready_table_image_vlm_jobs(
        runtime=table_image_vlm_runtime,
        executor=table_image_vlm_executor,
        jobs=table_image_vlm_jobs,
        markdown_parts=markdown_parts,
        warnings=warnings,
        force=True,
    )
    table_image_vlm.finalize_table_image_vlm_jobs(
        artifact_dir=artifact_dir,
        jobs=table_image_vlm_jobs,
        markdown_parts=markdown_parts,
        warnings=warnings,
    )
    persist_table_data_toon_artifacts(
        artifact_dir=artifact_dir,
        table_image_vlm_jobs=table_image_vlm_jobs,
        resolved_file_id=resolved_file_id,
        file_name=file_name,
        warnings=warnings,
    )
    table_image_vlm_executor.shutdown(wait=True)


def build_layout_outputs(
    *,
    layout: dict[str, Any],
    markdown_parts: list[str],
    structured_block_metadata: list[dict[str, Any]],
    images: list[ExtractedImageArtifact],
    warnings: list[str],
    partial_failures: list[Any],
    table_image_count: int,
    s3_upload_failed_count: int,
    s3_upload_uploaded_count: int,
    s3_upload_skipped_count: int,
) -> dict[str, Any]:
    """
    Build the stable processing output payload consumed by pipeline/extractor call sites.
    """

    structured_blocks = markdown_builder.build_structured_blocks(
        structured_block_metadata=structured_block_metadata,
        markdown_parts=markdown_parts,
    )

    from app.service.rag.ingestion.docling.models import DoclingParseStats

    stats = DoclingParseStats(
        converted_chunks=int(layout.get("converted_chunks", 0) or 0),
        partial_failure_chunks=len(partial_failures),
        pictures_extracted=sum(1 for item in images if item.kind == "picture"),
        table_fallback_images_extracted=table_image_count,
    )

    return {
        "markdown_text": "\n\n".join(markdown_parts),
        "structured_blocks": structured_blocks,
        "images": images,
        "warnings": warnings,
        "partial_failures": partial_failures,
        "stats": stats,
        "s3_upload_failed_count": s3_upload_failed_count,
        "s3_upload_uploaded_count": s3_upload_uploaded_count,
        "s3_upload_skipped_count": s3_upload_skipped_count,
    }
