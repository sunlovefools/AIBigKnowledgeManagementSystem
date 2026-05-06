"""
Docling extraction pipeline orchestrator.

Flow:
1. Load raw Docling layout from selected backend client.
2. Delegate layout item processing to shared layout-processing package.
3. Build parse result + manifest payload for callers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.service.rag.ingestion.docling.clients import beam_client, local_client
from app.service.rag.ingestion.docling.config import DEFAULT_DOCLING_PAGE_CHUNK_SIZE
from app.service.rag.ingestion.docling.layout_processing import process_docling_layout
from app.service.rag.ingestion.docling.models import DoclingParseResult
from app.service.rag.ingestion.docling.storage import local_artifacts_store

def parse_pdf_with_docling(
    pdf_bytes: bytes,
    file_name: str,
    page_chunk_size: int = DEFAULT_DOCLING_PAGE_CHUNK_SIZE,
    file_id: str | None = None,
    run_id: str | None = None,
    artifact_dir: Path | None = None,
    markdown_path: Path | None = None,
    backend: str | None = None,
) -> DoclingParseResult:
    """
    Parse a PDF with Docling and persist artifacts (markdown + extracted images).
    """

    if not pdf_bytes:
        raise ValueError("empty pdf payload")

    selected_backend = (backend or "beam").strip().lower()
    print(f"[docling-pipeline] start file={file_name} with backend={selected_backend}")

    # Docling-pdf pipeline 1: Resolve file id for downstream processing and artifact persistence.
    # Artifact here is to store all the output from this pipeline for debugging and traceability, it is not a required component for the pipeline to run successfully.
    resolved_file_id = (file_id or "").strip()
    resolved_run_id = (run_id or "").strip()
    artifacts_enabled = artifact_dir is not None and markdown_path is not None

    warnings: list[str] = []
    partial_failures: list[Any] = []

    print("[docling-pipeline] loading raw layout...")
    # Docling-pdf pipeline 2: Load raw Docling layout from selected backend client.
    if selected_backend == "local":
        layout = local_client.build_local_layout(
            pdf_bytes=pdf_bytes,
            file_name=file_name,
            page_chunk_size=page_chunk_size,
            warnings=warnings,
            partial_failures=partial_failures,
        )
        empty_markdown_error = (
            "No pages converted successfully with local Docling. "
            "Try Beam backend or a smaller local chunk size."
        )
    else:
        layout = beam_client.build_beam_layout(
            pdf_bytes=pdf_bytes,
            file_name=file_name,
            warnings=warnings,
            partial_failures=partial_failures,
        )
        empty_markdown_error = (
            "No markdown text serialized from Beam Docling endpoint response."
        )
    print(f"[docling-pipeline] layout loaded: items={len(layout['items'])}")

    # Docling-pdf pipeline 3: Process the normalized layout with the shared layout processor to emit structured blocks and markdown.
    outputs = process_docling_layout(
        layout=layout,
        file_name=file_name,
        resolved_file_id=resolved_file_id,
        artifact_dir=artifact_dir,
        markdown_path=markdown_path,
        image_export_mode=selected_backend,
        warnings=warnings,
        partial_failures=partial_failures,
        pdf_bytes=pdf_bytes if selected_backend == "beam" else None,
        empty_markdown_error=empty_markdown_error,
    )

    # Docling-pdf pipeline 4: Build the final parse result model and persist manifest artifact for debugging and traceability.
    result_model = DoclingParseResult(
        warnings=outputs["warnings"],
        partial_failures=outputs["partial_failures"],
        structured_blocks=outputs["structured_blocks"],
    )

    # Docling-pdf-pipeline 5: If artifacts are enabled, persist a manifest JSON file containing the parse outputs for debugging and traceability.
    if artifacts_enabled and artifact_dir is not None:
        local_artifacts_store.write_manifest(
            artifact_dir,
            {
                "source_file_name": file_name,
                "artifact_run_id": resolved_run_id,
                "artifact_dir": str(artifact_dir),
                "markdown_path": str(markdown_path),
                "markdown_text": outputs["markdown_text"],
                "images": [image.model_dump() for image in outputs["images"]],
                "warnings": outputs["warnings"],
                "partial_failures": [
                    failure.model_dump()
                    if hasattr(failure, "model_dump")
                    else str(failure)
                    for failure in outputs["partial_failures"]
                ],
                "stats": outputs["stats"].model_dump(),
                "structured_blocks": [
                    block.model_dump() for block in outputs["structured_blocks"]
                ],
            },
        )

    print(
        "[docling-pipeline] file=%s backend=%s run_id=%s chunks=%s pictures=%s "
        "table_fallbacks=%s partial_failures=%s"
        % (
            file_name,
            selected_backend,
            resolved_run_id,
            outputs["stats"].converted_chunks,
            outputs["stats"].pictures_extracted,
            outputs["stats"].table_fallback_images_extracted,
            outputs["stats"].partial_failure_chunks,
        )
    )
    return result_model
