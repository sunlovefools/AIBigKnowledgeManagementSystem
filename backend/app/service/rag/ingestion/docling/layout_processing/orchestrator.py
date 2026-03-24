"""
Shared Docling layout processing orchestrator.

This module is used by both PDF and Office Docling extraction flows.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import fitz
from uuid6 import uuid6

from app.service.rag.ingestion.docling import table_image_vlm
from app.service.rag.ingestion.docling.config import DOCLING_IMAGE_CROP_FAILED_MARKER
from app.service.rag.ingestion.docling.models import ExtractedImageArtifact
from app.service.rag.ingestion.docling.storage import local_artifacts_store
from app.service.rag.ingestion.docling.utils import markdown_builder
from app.service.rag.ingestion.docling.layout_processing.classification import (
    block_type_for_element,
)
from app.service.rag.ingestion.docling.layout_processing.image_export import (
    S3UploadCounters,
    extract_png_bytes_for_item,
    prepare_picture_artifact_paths,
    upload_extracted_image_artifact,
)
from app.service.rag.ingestion.docling.layout_processing.lifecycle import (
    build_layout_outputs,
    finalize_table_image_jobs,
)


def process_docling_layout(
    *,
    layout: dict[str, Any],
    file_name: str,
    resolved_file_id: str,
    artifact_dir: Path | None,
    markdown_path: Path | None,
    image_export_mode: str,
    warnings: list[str],
    partial_failures: list[Any],
    pdf_bytes: bytes | None = None,
    empty_markdown_error: str,
) -> dict[str, Any]:
    """
    Process a normalized Docling layout into markdown, structured blocks, images, and stats.
    """

    artifacts_enabled = artifact_dir is not None and markdown_path is not None

    markdown_parts: list[str] = []
    structured_block_metadata: list[dict[str, Any]] = []
    images: list[ExtractedImageArtifact] = []
    table_image_vlm_jobs: list[table_image_vlm.TableImageVlmJob] = []
    emitted_sheet_headers: set[str] = set()
    upload_counters = S3UploadCounters()

    picture_counter = 0
    table_counter = 0
    table_image_count = 0

    picture_item_cls = layout["picture_item_cls"]
    table_item_cls = layout["table_item_cls"]
    list_item_cls = layout.get("list_item_cls")
    section_header_item_cls = layout.get("section_header_item_cls")
    title_item_cls = layout.get("title_item_cls")

    table_image_vlm_runtime = (
        table_image_vlm.build_table_image_vlm_runtime(
            artifact_dir=artifact_dir,
            warnings=warnings,
        )
        if artifacts_enabled and artifact_dir is not None
        else None
    )

    table_image_vlm_executor: ThreadPoolExecutor | None = None
    if table_image_vlm_runtime is not None:
        table_image_vlm_executor = ThreadPoolExecutor(
            max_workers=table_image_vlm_runtime.max_workers,
            thread_name_prefix="table-vlm",
        )

    pdf_doc: fitz.Document | None = None
    if image_export_mode == "beam":
        if not pdf_bytes:
            raise ValueError("pdf bytes are required when image_export_mode='beam'")
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    try:
        for item in layout["items"]:
            element = item["element"]
            serializer = item["serializer"]
            page_no = item.get("page_no")
            sheet_name = str(item.get("sheet_name") or "").strip()
            sheet_ref = str(item.get("sheet_ref") or "").strip()
            sheet_key = sheet_ref or f"sheet-name::{sheet_name}"

            # Excel sheet-aware preamble: emit one header block per sheet at first appearance.
            if sheet_name and sheet_key not in emitted_sheet_headers:
                markdown_builder.append_markdown_block(
                    markdown_parts=markdown_parts,
                    structured_block_metadata=structured_block_metadata,
                    text=f"# Sheet: {sheet_name}",
                    block_type="header",
                    page_no=page_no,
                    is_table_image=False,
                )
                emitted_sheet_headers.add(sheet_key)

            is_picture_item = isinstance(element, picture_item_cls)
            is_table_item = isinstance(element, table_item_cls)
            picture_markdown_placeholder: str | None = None

            if is_picture_item:
                picture_markdown_placeholder = DOCLING_IMAGE_CROP_FAILED_MARKER
                picture_counter += 1
                if artifacts_enabled and artifact_dir is not None:
                    image_uuid, picture_name, picture_path = prepare_picture_artifact_paths(
                        artifact_dir=artifact_dir
                    )
                    try:
                        png_bytes = extract_png_bytes_for_item(
                            image_export_mode=image_export_mode,
                            item=item,
                            element=element,
                            pdf_doc=pdf_doc,
                        )
                        if not png_bytes:
                            if image_export_mode == "beam":
                                raise RuntimeError(
                                    "missing/invalid bbox or crop produced no pixels"
                                )
                            raise RuntimeError("Docling picture image unavailable.")

                        picture_path.write_bytes(png_bytes)
                        image_artifact = ExtractedImageArtifact(
                            kind="picture",
                            image_uuid=image_uuid,
                            file_name=picture_name,
                            file_path=str(picture_path),
                            page_no=page_no,
                            picture_index=picture_counter,
                        )
                        image_artifact = upload_extracted_image_artifact(
                            image_artifact=image_artifact,
                            source_file_name=file_name,
                            file_id=resolved_file_id,
                            warnings=warnings,
                            counters=upload_counters,
                        )

                        images.append(image_artifact)
                        picture_markdown_placeholder = markdown_builder.picture_uuid_marker(
                            image_artifact.image_uuid
                        )
                    except Exception as exc:
                        prefix = "local " if image_export_mode == "local" else ""
                        warnings.append(
                            f"Failed to export {prefix}picture #{picture_counter} on page {page_no}: {exc}"
                        )
                else:
                    # When artifacts are disabled, we still emit a synthetic marker so downstream chunking keeps the visual placeholder behavior.
                    picture_markdown_placeholder = markdown_builder.picture_uuid_marker(
                        str(uuid6())
                    )

            if is_table_item:
                table_counter += 1
                table_index = table_counter
                num_rows = item.get("num_rows")
                num_cols = item.get("num_cols")

                if num_rows == 0 or num_cols == 0:
                    # Zero-row/column tables are treated as table-image fallbacks and can optionally trigger VLM summarization.
                    table_image_count += 1
                    if artifacts_enabled and artifact_dir is not None:
                        image_uuid, table_image_name, table_image_path = prepare_picture_artifact_paths(
                            artifact_dir=artifact_dir
                        )
                        try:
                            png_bytes = extract_png_bytes_for_item(
                                image_export_mode=image_export_mode,
                                item=item,
                                element=element,
                                pdf_doc=pdf_doc,
                            )
                            if not png_bytes:
                                if image_export_mode == "beam":
                                    raise RuntimeError(
                                        "missing/invalid bbox or crop produced no pixels"
                                    )
                                raise RuntimeError("Docling table image unavailable.")

                            table_image_path.write_bytes(png_bytes)
                            image_artifact = ExtractedImageArtifact(
                                kind="table_image",
                                image_uuid=image_uuid,
                                file_name=table_image_name,
                                file_path=str(table_image_path),
                                page_no=page_no,
                                table_index=table_index,
                                reason="table_rows_cols_zero",
                            )
                            image_artifact = upload_extracted_image_artifact(
                                image_artifact=image_artifact,
                                source_file_name=file_name,
                                file_id=resolved_file_id,
                                warnings=warnings,
                                counters=upload_counters,
                            )

                            images.append(image_artifact)
                            table_markdown_lines = [
                                "> **Table (image)**: Table exists in image form.",
                                f"> {markdown_builder.table_image_uuid_marker(image_artifact.image_uuid)}",
                                f"> ![{table_image_name}]({local_artifacts_store.image_markdown_rel_path_from_uuid(image_artifact.image_uuid)})",
                            ]

                            if (
                                table_image_vlm_runtime is not None
                                and table_image_vlm_executor is not None
                            ):
                                # VLM job lifecycle: add a summary placeholder now, then replace/finalize during forced flush in finally.
                                summary_placeholder = (
                                    table_image_vlm.table_image_vlm_summary_placeholder(
                                        image_artifact.image_uuid
                                    )
                                )
                                table_image_vlm_jobs.append(
                                    table_image_vlm.TableImageVlmJob(
                                        image_artifact=image_artifact,
                                        table_index=table_index,
                                        page_no=page_no,
                                        block_index=len(markdown_parts),
                                        summary_placeholder=summary_placeholder,
                                        output_dir=table_image_vlm.table_image_vlm_output_dir(
                                            artifact_dir,
                                            table_index=table_index,
                                            image_uuid=image_artifact.image_uuid,
                                        ),
                                        json_rel_path=table_image_vlm.table_image_vlm_json_rel_path(
                                            table_index=table_index,
                                            image_uuid=image_artifact.image_uuid,
                                        ),
                                    )
                                )
                                table_markdown_lines.append(f"> {summary_placeholder}")

                            markdown_builder.append_markdown_block(
                                markdown_parts=markdown_parts,
                                structured_block_metadata=structured_block_metadata,
                                text="\n".join(table_markdown_lines),
                                block_type="table",
                                page_no=page_no,
                                is_table_image=True,
                                table_image_uuid=image_artifact.image_uuid,
                            )
                            table_image_vlm.submit_ready_table_image_vlm_jobs(
                                runtime=table_image_vlm_runtime,
                                executor=table_image_vlm_executor,
                                jobs=table_image_vlm_jobs,
                                markdown_parts=markdown_parts,
                                warnings=warnings,
                            )
                        except Exception as exc:
                            prefix = "local " if image_export_mode == "local" else ""
                            message = (
                                "> (Local image export failed.)"
                                if image_export_mode == "local"
                                else "> (Local crop failed: missing/invalid bbox or page_no.)"
                            )
                            warnings.append(
                                f"Failed to export {prefix}fallback table image #{table_index} on page {page_no}: {exc}"
                            )
                            markdown_builder.append_markdown_block(
                                markdown_parts=markdown_parts,
                                structured_block_metadata=structured_block_metadata,
                                text="\n".join(
                                    [
                                        "> **Table (image)**: Table exists in image form.",
                                        message,
                                    ]
                                ),
                                block_type="table",
                                page_no=page_no,
                                is_table_image=True,
                            )
                    else:
                        # Artifact-disabled mode: still emit the semantic table-image block to preserve downstream behavior.
                        markdown_builder.append_markdown_block(
                            markdown_parts=markdown_parts,
                            structured_block_metadata=structured_block_metadata,
                            text="> **Table (image)**: Table exists in image form.",
                            block_type="table",
                            page_no=page_no,
                            is_table_image=True,
                        )
                    continue

            try:
                serialized_text = serializer.serialize(item=element).text.strip()
            except Exception as exc:
                prefix = "local " if image_export_mode == "local" else ""
                warnings.append(
                    f"Failed to serialize {prefix}element on page {page_no}: {exc}"
                )
                continue

            if picture_markdown_placeholder is not None:
                serialized_text = markdown_builder.inject_marker_for_picture(
                    serialized_text,
                    picture_markdown_placeholder,
                )

            if serialized_text:
                markdown_builder.append_markdown_block(
                    markdown_parts=markdown_parts,
                    structured_block_metadata=structured_block_metadata,
                    text=serialized_text,
                    block_type=block_type_for_element(
                        element,
                        picture_item_cls=picture_item_cls,
                        table_item_cls=table_item_cls,
                        list_item_cls=list_item_cls,
                        section_header_item_cls=section_header_item_cls,
                        title_item_cls=title_item_cls,
                    ),
                    page_no=page_no,
                    is_table_image=False,
                )

                table_image_vlm.submit_ready_table_image_vlm_jobs(
                    runtime=table_image_vlm_runtime,
                    executor=table_image_vlm_executor,
                    jobs=table_image_vlm_jobs,
                    markdown_parts=markdown_parts,
                    warnings=warnings,
                )
    finally:
        if pdf_doc is not None:
            pdf_doc.close()

        # Finalization is intentionally forced in finally so pending table-image jobs/placeholder reconciliation always runs.
        finalize_table_image_jobs(
            artifact_dir=artifact_dir,
            table_image_vlm_runtime=table_image_vlm_runtime,
            table_image_vlm_executor=table_image_vlm_executor,
            table_image_vlm_jobs=table_image_vlm_jobs,
            markdown_parts=markdown_parts,
            warnings=warnings,
            resolved_file_id=resolved_file_id,
            file_name=file_name,
        )

    if not markdown_parts:
        raise RuntimeError(empty_markdown_error)

    if artifacts_enabled and markdown_path is not None:
        markdown_path.write_text("\n\n".join(markdown_parts), encoding="utf-8")

    return build_layout_outputs(
        layout=layout,
        markdown_parts=markdown_parts,
        structured_block_metadata=structured_block_metadata,
        images=images,
        warnings=warnings,
        partial_failures=partial_failures,
        table_image_count=table_image_count,
        s3_upload_failed_count=upload_counters.failed_count,
        s3_upload_uploaded_count=upload_counters.uploaded_count,
        s3_upload_skipped_count=upload_counters.skipped_count,
    )
