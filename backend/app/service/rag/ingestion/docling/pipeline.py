"""
Docling extraction pipeline orchestrator.

Flow:
1. Load raw Docling layout from selected backend client.
2. Loop through layout items once.
3. Export images (crop/extract -> local artifact -> optional S3 upload).
4. Queue table-image blocks to table-image VLM.
5. Build markdown blocks and parse result artifacts.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import fitz
from uuid6 import uuid6

from app.service.rag.ingestion.docling import table_image_vlm
from app.service.rag.ingestion.docling.clients import beam_client, local_client
from app.service.rag.ingestion.docling.config import (
    DEFAULT_DOCLING_PAGE_CHUNK_SIZE,
    DOCLING_IMAGE_CROP_FAILED_MARKER,
)
from app.service.rag.ingestion.docling.models import (
    DoclingParseResult,
    DoclingParseStats,
    ExtractedImageArtifact,
)
from app.service.rag.ingestion.docling.storage import local_artifacts_store, s3_upload
from app.service.rag.ingestion.docling.utils import markdown_builder, pdf_utils
from app.service.rag.ingestion.docling.utils.table_data_artifacts import (
    persist_table_data_toon_artifacts,
)
from app.service.rag.ingestion.markdown_canonicalizer import canonicalize_docling_block_text


def _block_type_for_element(
    element: Any,
    *,
    picture_item_cls: Any,
    table_item_cls: Any,
    list_item_cls: Any,
    section_header_item_cls: Any,
    title_item_cls: Any,
) -> str:
    """Classify a Docling element into normalized block categories."""

    if title_item_cls is not None and isinstance(element, title_item_cls):
        return "header"
    if section_header_item_cls is not None and isinstance(element, section_header_item_cls):
        return "header"
    if list_item_cls is not None and isinstance(element, list_item_cls):
        return "list"
    if isinstance(element, picture_item_cls):
        return "picture"
    if isinstance(element, table_item_cls):
        return "table"
    if hasattr(element, "text"):
        return "text"
    return "other"


def _extract_png_bytes_for_item(
    *,
    image_export_mode: str,
    item: dict[str, Any],
    element: Any,
    pdf_doc: fitz.Document | None,
) -> bytes | None:
    """Extract PNG bytes from a Docling item using the selected image export mode."""

    if image_export_mode == "beam":
        endpoint_item = item.get("endpoint_item", {})
        if pdf_doc is None:
            return None
        return pdf_utils.crop_image_bytes_from_endpoint_item(endpoint_item, pdf_doc)

    return local_client._extract_png_bytes_from_local_element(
        element,
        item.get("document"),
    )


def _process_docling_layout(
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

    This is the shared processing core used by both PDF and Office extraction paths.
    TODO: This must be refactor, I think it should have its own module and not be coupled with the pipeline.py
    """

    artifacts_enabled = artifact_dir is not None and markdown_path is not None

    markdown_parts: list[str] = []
    structured_block_metadata: list[dict[str, Any]] = []
    images: list[ExtractedImageArtifact] = []
    table_image_vlm_jobs: list[table_image_vlm.TableImageVlmJob] = []
    emitted_sheet_headers: set[str] = set()

    s3_upload_failed_count = 0
    s3_upload_uploaded_count = 0
    s3_upload_skipped_count = 0

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
                    image_uuid = str(uuid6())
                    picture_name = local_artifacts_store.image_file_name_from_uuid(image_uuid)
                    picture_path = local_artifacts_store.image_file_path_from_uuid(
                        artifact_dir,
                        image_uuid,
                    )
                    try:
                        png_bytes = _extract_png_bytes_for_item(
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
                        image_artifact = s3_upload.upload_image_artifact_to_s3(
                            image_artifact,
                            source_file_name=file_name,
                            file_id=resolved_file_id,
                        )

                        if image_artifact.s3_upload_status == "failed":
                            s3_upload_failed_count += 1
                            warnings.append(
                                f"Failed to upload picture image_uuid={image_artifact.image_uuid} to S3: {image_artifact.s3_error}"
                            )
                        elif image_artifact.s3_upload_status == "uploaded":
                            s3_upload_uploaded_count += 1
                        elif image_artifact.s3_upload_status == "skipped":
                            s3_upload_skipped_count += 1

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
                    picture_markdown_placeholder = markdown_builder.picture_uuid_marker(
                        str(uuid6())
                    )

            if is_table_item:
                table_counter += 1
                table_index = table_counter
                num_rows = item.get("num_rows")
                num_cols = item.get("num_cols")

                if num_rows == 0 or num_cols == 0:
                    table_image_count += 1
                    if artifacts_enabled and artifact_dir is not None:
                        image_uuid = str(uuid6())
                        table_image_name = local_artifacts_store.image_file_name_from_uuid(
                            image_uuid
                        )
                        table_image_path = local_artifacts_store.image_file_path_from_uuid(
                            artifact_dir,
                            image_uuid,
                        )
                        try:
                            png_bytes = _extract_png_bytes_for_item(
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
                            image_artifact = s3_upload.upload_image_artifact_to_s3(
                                image_artifact,
                                source_file_name=file_name,
                                file_id=resolved_file_id,
                            )

                            if image_artifact.s3_upload_status == "failed":
                                s3_upload_failed_count += 1
                                warnings.append(
                                    "Failed to upload table image "
                                    f"image_uuid={image_artifact.image_uuid} to S3: {image_artifact.s3_error}"
                                )
                            elif image_artifact.s3_upload_status == "uploaded":
                                s3_upload_uploaded_count += 1
                            elif image_artifact.s3_upload_status == "skipped":
                                s3_upload_skipped_count += 1

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
                    block_type=_block_type_for_element(
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
        if table_image_vlm_executor is not None:
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

    if not markdown_parts:
        raise RuntimeError(empty_markdown_error)

    canonicalized_markdown_parts = list(markdown_parts)
    for metadata in structured_block_metadata:
        block_index = int(metadata.get("block_index", -1))
        if block_index < 0 or block_index >= len(canonicalized_markdown_parts):
            continue
        block_type = str(metadata.get("block_type") or "text")
        canonicalized_markdown_parts[block_index] = canonicalize_docling_block_text(
            block_type=block_type,
            text=canonicalized_markdown_parts[block_index],
        )

    markdown_parts = canonicalized_markdown_parts
    markdown_text = "\n\n".join(markdown_parts)
    if artifacts_enabled and markdown_path is not None:
        markdown_path.write_text(markdown_text, encoding="utf-8")

    structured_blocks = markdown_builder.build_structured_blocks(
        structured_block_metadata=structured_block_metadata,
        markdown_parts=markdown_parts,
    )

    stats = DoclingParseStats(
        converted_chunks=int(layout.get("converted_chunks", 0) or 0),
        partial_failure_chunks=len(partial_failures),
        pictures_extracted=sum(1 for item in images if item.kind == "picture"),
        table_fallback_images_extracted=table_image_count,
    )

    return {
        "markdown_text": markdown_text,
        "structured_blocks": structured_blocks,
        "images": images,
        "warnings": warnings,
        "partial_failures": partial_failures,
        "stats": stats,
        "s3_upload_failed_count": s3_upload_failed_count,
        "s3_upload_uploaded_count": s3_upload_uploaded_count,
        "s3_upload_skipped_count": s3_upload_skipped_count,
    }


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
    partial_failures = []

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

    # Docling-pdf pipeline 3: Process the normalized layout with the shared Docling pipeline core to emit structured blocks and markdown.
    outputs = _process_docling_layout(
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
        "table_fallbacks=%s partial_failures=%s s3_uploaded=%s s3_failed=%s s3_skipped=%s"
        % (
            file_name,
            selected_backend,
            resolved_run_id,
            outputs["stats"].converted_chunks,
            outputs["stats"].pictures_extracted,
            outputs["stats"].table_fallback_images_extracted,
            outputs["stats"].partial_failure_chunks,
            outputs["s3_upload_uploaded_count"],
            outputs["s3_upload_failed_count"],
            outputs["s3_upload_skipped_count"],
        )
    )
    return result_model
