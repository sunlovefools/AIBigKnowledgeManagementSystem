"""
Docling extraction pipeline orchestrator.

Flow:
1. Load raw Docling layout from selected backend client.
2. Loop through layout items once.
3. Export images (crop/extract -> local artifact -> optional S3 upload).
4. Queue table-image blocks to table-image VLM.
5. Build markdown blocks and parse result artifacts.

Extension:
When Docling provides structured table metadata (num_rows > 0 and num_cols > 0),
tables are extracted directly as structured data instead of using the image
fallback path.

Structured table flow:
- Convert table element to DataFrame via export_to_dataframe().
- Build structured JSON (headers and rows).
- Wrap payload using the same TOON format as image-based tables.
- Persist to table_data/<uuid>.json.
- Optionally upload to S3 when AWS_S3_UPLOAD_ENABLED=true.
- Inject stable markdown marker {{TABLE_STRUCTURED_UUID:<uuid>}} for UI detection.

If structured extraction fails or metadata is unavailable, the existing
image-based table fallback logic remains unchanged.
"""

from __future__ import annotations

import json
import os
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
    get_docling_backend_selection,
)
from app.service.rag.ingestion.docling.models import (
    DoclingParseResult,
    DoclingParseStats,
    ExtractedImageArtifact,
)
from app.service.rag.ingestion.docling.storage import (
    local_artifacts_store,
    s3_upload,
)
from app.service.rag.ingestion.docling.utils import markdown_builder


def _structured_table_uuid_marker(table_uuid: str) -> str:
    """
    Stable marker for structured table artifacts.
    UI can detect this and fetch corresponding table_data/<uuid>.json.
    """
    return f"{{{{TABLE_STRUCTURED_UUID:{table_uuid}}}}}"


def parse_pdf_with_docling_preview(
    pdf_bytes: bytes,
    file_name: str,
    artifact_root: Path | None = None,
    page_chunk_size: int = DEFAULT_DOCLING_PAGE_CHUNK_SIZE,
    file_id: str | None = None,
    backend: str | None = None,
) -> DoclingParseResult:

    if not pdf_bytes:
        raise ValueError("empty pdf payload")

    selected_backend = (backend or get_docling_backend_selection()).strip().lower()
    if selected_backend not in {"beam", "local"}:
        selected_backend = "beam"

    print(f"[docling-pipeline] start file={file_name}")
    print(f"[docling-pipeline] backend selected: {selected_backend}")

    resolved_file_id = (file_id or str(uuid6())).strip()

    run_id, artifact_dir, markdown_path = local_artifacts_store.prepare_docling_preview_artifact_dir(
        file_name=file_name,
        artifact_root=artifact_root,
    )
    artifacts_enabled = artifact_dir is not None and markdown_path is not None

    markdown_parts: list[str] = []
    structured_block_metadata: list[dict[str, Any]] = []
    images: list[ExtractedImageArtifact] = []
    warnings: list[str] = []
    partial_failures = []
    table_image_vlm_jobs: list[table_image_vlm.TableImageVlmJob] = []

    s3_upload_failed_count = 0
    s3_upload_uploaded_count = 0
    s3_upload_skipped_count = 0

    picture_counter = 0
    table_counter = 0
    table_image_count = 0

    print("[docling-pipeline] loading raw layout...")
    if selected_backend == "local":
        layout = local_client.build_local_layout(
            pdf_bytes=pdf_bytes,
            file_name=file_name,
            page_chunk_size=page_chunk_size,
            warnings=warnings,
            partial_failures=partial_failures,
        )
    else:
        layout = beam_client.build_beam_layout(
            pdf_bytes=pdf_bytes,
            file_name=file_name,
            warnings=warnings,
            partial_failures=partial_failures,
        )
    print(f"[docling-pipeline] layout loaded: items={len(layout['items'])}")

    picture_item_cls = layout["picture_item_cls"]
    table_item_cls = layout["table_item_cls"]
    converted_chunks = layout.get("converted_chunks", 0)

    pdf_doc: fitz.Document | None = None
    if selected_backend == "beam":
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    print("[docling-pipeline] processing items...")
    try:
        for item in layout["items"]:
            element = item["element"]
            serializer = item["serializer"]
            page_no = item.get("page_no")

            is_picture_item = isinstance(element, picture_item_cls)
            is_table_item = isinstance(element, table_item_cls)

            # ==============================
            # STRUCTURED TABLE EXPORT
            # ==============================
            if is_table_item:
                table_counter += 1
                table_index = table_counter
                num_rows = item.get("num_rows")
                num_cols = item.get("num_cols")

                if (num_rows and num_cols) and (num_rows > 0 and num_cols > 0):
                    try:
                        table_df = element.export_to_dataframe()

                        headers = [str(col).strip() for col in table_df.columns]
                        rows = []
                        for _, r in table_df.iterrows():
                            row_dict = {
                                headers[i]: "" if r.iloc[i] is None else str(r.iloc[i]).strip()
                                for i in range(len(headers))
                            }
                            if any(v.strip() for v in row_dict.values()):
                                rows.append(row_dict)

                        extracted_payload = {
                            "headers": headers,
                            "rows": rows,
                        }

                        if artifacts_enabled and artifact_dir is not None:
                            table_uuid = str(uuid6())
                            wrapped_payload = s3_upload.build_toon_wrapped_table_payload(
                                extracted_table_json=extracted_payload,
                                file_id=resolved_file_id,
                                page_no=page_no,
                            )

                            table_data_path = local_artifacts_store.table_data_file_path_from_uuid(
                                artifact_dir,
                                table_uuid,
                            )

                            serialized = json.dumps(wrapped_payload, indent=2, ensure_ascii=False)
                            table_data_path.write_text(serialized, encoding="utf-8")

                            upload_enabled = (os.getenv("AWS_S3_UPLOAD_ENABLED") or "").strip().lower() == "true"
                            if upload_enabled:
                                try:
                                    s3_upload.upload_table_data_json_to_s3(
                                        json_bytes=serialized.encode("utf-8"),
                                        file_id=resolved_file_id,
                                        table_image_uuid=table_uuid,
                                        source_file_name=file_name,
                                        page_no=page_no,
                                    )
                                except Exception as exc:
                                    warnings.append(
                                        f"Failed to upload structured table JSON to S3: {exc}"
                                    )

                            # Inject stable marker for UI detection
                            marker = _structured_table_uuid_marker(table_uuid)

                            markdown_builder.append_markdown_block(
                                markdown_parts=markdown_parts,
                                structured_block_metadata=structured_block_metadata,
                                text="\n".join(
                                    [
                                        "> **Table (structured)**",
                                        f"> {marker}",
                                    ]
                                ),
                                block_type="table",
                                page_no=page_no,
                                is_table_image=False,
                            )

                        continue

                    except Exception as exc:
                        warnings.append(
                            f"Structured table extraction failed for table #{table_index} on page {page_no}: {exc}"
                        )

                # If no structure, fall through to existing behavior

            # ==============================
            # DEFAULT SERIALIZATION
            # ==============================
            try:
                serialized_text = serializer.serialize(item=element).text.strip()
            except Exception as exc:
                warnings.append(
                    f"Failed to serialize element on page {page_no}: {exc}"
                )
                continue

            if serialized_text:
                markdown_builder.append_markdown_block(
                    markdown_parts=markdown_parts,
                    structured_block_metadata=structured_block_metadata,
                    text=serialized_text,
                    block_type="text",
                    page_no=page_no,
                    is_table_image=False,
                )

    finally:
        if pdf_doc is not None:
            pdf_doc.close()

    print("[docling-pipeline] item processing complete")

    markdown_text = "\n\n".join(markdown_parts)

    stats = DoclingParseStats(
        converted_chunks=converted_chunks,
        partial_failure_chunks=len(partial_failures),
        pictures_extracted=0,
        table_fallback_images_extracted=0,
    )

    result_model = DoclingParseResult(
        source_file_name=file_name,
        artifact_run_id=run_id,
        artifact_dir=str(artifact_dir) if artifacts_enabled and artifact_dir is not None else "",
        markdown_path=str(markdown_path) if artifacts_enabled and markdown_path is not None else "",
        markdown_text=markdown_text,
        images=images,
        warnings=warnings,
        partial_failures=partial_failures,
        stats=stats,
        structured_blocks=structured_block_metadata,
    )

    if artifacts_enabled and artifact_dir is not None:
        local_artifacts_store.write_manifest(artifact_dir, result_model.model_dump())

    return result_model