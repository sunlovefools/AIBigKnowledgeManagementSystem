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
from app.service.rag.ingestion.docling.storage import (
    local_artifacts_store,
    s3_upload,
)
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


def parse_pdf_with_docling(
    pdf_bytes: bytes,
    file_name: str,
    artifact_root: Path | None = None,
    page_chunk_size: int = DEFAULT_DOCLING_PAGE_CHUNK_SIZE,
    file_id: str | None = None,
    backend: str | None = None,
) -> DoclingParseResult:
    """
    Parse a PDF with Docling and persist artifacts (markdown + extracted images).
    """

    if not pdf_bytes:
        raise ValueError("empty pdf payload")

    # Decide which backend service to use for docling processing
    selected_backend = (backend or "beam").strip().lower()
    print(f"[docling-pipeline] start file={file_name}")
    print(f"[docling-pipeline] backend selected: {selected_backend}")

    resolved_file_id = (file_id or "").strip()

    # Prepare artifact directory and paths for markdown and extracted images.
    run_id, artifact_dir, markdown_path = local_artifacts_store.prepare_docling_artifact_dir(
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
    # Use the selected backend client to process the PDF.
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

    # Extract all the items out from the docling process
    picture_item_cls = layout["picture_item_cls"]
    table_item_cls = layout["table_item_cls"]
    list_item_cls = layout.get("list_item_cls")
    section_header_item_cls = layout.get("section_header_item_cls")
    title_item_cls = layout.get("title_item_cls")
    converted_chunks = layout.get("converted_chunks", 0)

    # Build table-image VLM runtime (For using an LLM to build summary and extract the data out) if it is yet to be built.
    table_image_vlm_runtime = (
        table_image_vlm.build_table_image_vlm_runtime(
            artifact_dir=artifact_dir,
            warnings=warnings,
        )
        if artifacts_enabled and artifact_dir is not None
        else None
    )

    # Create a thread pool to allow background worker to run table-image VLM jobs in parallel
    table_image_vlm_executor: ThreadPoolExecutor | None = None
    if table_image_vlm_runtime is not None:
        table_image_vlm_executor = ThreadPoolExecutor(
            max_workers=table_image_vlm_runtime.max_workers,
            thread_name_prefix="table-vlm", # For logging and debugging purposes
        )

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
            picture_markdown_placeholder: str | None = None

            if is_picture_item:
                picture_markdown_placeholder = DOCLING_IMAGE_CROP_FAILED_MARKER
                picture_counter += 1
                if artifacts_enabled and artifact_dir is not None:
                    # Create an artifact for the extracted picture image
                    image_uuid = str(uuid6())
                    picture_name = local_artifacts_store.image_file_name_from_uuid(image_uuid)
                    picture_path = local_artifacts_store.image_file_path_from_uuid(
                        artifact_dir,
                        image_uuid,
                    )
                    try:
                        # TODO: unify the image extraction logic between beam and local backends so we don't have to condition on the backend here.
                        # Extract the picture image bytes using the appropriate backend method
                        if selected_backend == "beam":
                            endpoint_item = item.get("endpoint_item", {})
                            png_bytes = pdf_utils.crop_image_bytes_from_endpoint_item(
                                endpoint_item,
                                pdf_doc,
                            )
                            if not png_bytes:
                                raise RuntimeError(
                                    "missing/invalid bbox or crop produced no pixels"
                                )
                        else:
                            png_bytes = local_client._extract_png_bytes_from_local_element(
                                element,
                                item.get("document"),
                            )
                            if not png_bytes:
                                raise RuntimeError("Docling picture image unavailable.")

                        # Write the extracted picture image to a local file
                        picture_path.write_bytes(png_bytes)
                        image_artifact = ExtractedImageArtifact(
                            kind="picture",
                            image_uuid=image_uuid,
                            file_name=picture_name,
                            file_path=str(picture_path),
                            page_no=page_no,
                            picture_index=picture_counter,
                        )
                        # Upload the extracted picture image artifact to S3 if enabled
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

                        # Create a markdown placeholder for the extracted image
                        picture_markdown_placeholder = markdown_builder.picture_uuid_marker(
                            image_artifact.image_uuid
                        )
                    except Exception as exc:
                        prefix = "local " if selected_backend == "local" else ""
                        warnings.append(
                            f"Failed to export {prefix}picture #{picture_counter} on page {page_no}: {exc}"
                        )
                else:
                    picture_markdown_placeholder = markdown_builder.picture_uuid_marker(
                        str(uuid6())
                    )

            # TODO: Refactor the entire table image handling logic into a separate function to avoid having this large block of code in the middle of the main loop
            if is_table_item:
                table_counter += 1
                table_index = table_counter
                num_rows = item.get("num_rows")
                num_cols = item.get("num_cols")

                # If the table has zero columns or rows then it is an table image
                if num_rows == 0 or num_cols == 0:
                    table_image_count += 1
                    if artifacts_enabled and artifact_dir is not None:
                        # Build the artifact for the extracted table image
                        image_uuid = str(uuid6())
                        table_image_name = local_artifacts_store.image_file_name_from_uuid(
                            image_uuid
                        )
                        table_image_path = local_artifacts_store.image_file_path_from_uuid(
                            artifact_dir,
                            image_uuid,
                        )
                        try:
                            # TODO: unify the image extraction logic between beam and local backends so we don't have to condition on the backend here.
                            # Uses the appropriate backend method to extract the table imahe bytes
                            if selected_backend == "beam":
                                endpoint_item = item.get("endpoint_item", {})
                                png_bytes = pdf_utils.crop_image_bytes_from_endpoint_item(
                                    endpoint_item,
                                    pdf_doc,
                                )
                                if not png_bytes:
                                    raise RuntimeError(
                                        "missing/invalid bbox or crop produced no pixels"
                                    )
                            else:
                                png_bytes = local_client._extract_png_bytes_from_local_element(
                                    element,
                                    item.get("document"),
                                )
                                if not png_bytes:
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
                                    f"Failed to upload table image image_uuid={image_artifact.image_uuid} to S3: {image_artifact.s3_error}"
                                )
                            elif image_artifact.s3_upload_status == "uploaded":
                                s3_upload_uploaded_count += 1
                            elif image_artifact.s3_upload_status == "skipped":
                                s3_upload_skipped_count += 1

                            images.append(image_artifact)
                            # Build the markdown block for the extracted table image, with optional summary placeholder if VLM is enabled
                            # TODO: Should refactor into a function to avoid having this logic in the middle of the main loop and to unify the logic between table image blocks and picture blocks
                            table_markdown_lines = [
                                "> **Table (image)**: Table exists in image form.",
                                f"> {markdown_builder.table_image_uuid_marker(image_artifact.image_uuid)}",
                                f"> ![{table_image_name}]({local_artifacts_store.image_markdown_rel_path_from_uuid(image_artifact.image_uuid)})",
                            ]

                            if table_image_vlm_runtime is not None and table_image_vlm_executor is not None:
                                summary_placeholder = table_image_vlm.table_image_vlm_summary_placeholder(
                                    image_artifact.image_uuid
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
                            prefix = "local " if selected_backend == "local" else ""
                            message = (
                                "> (Local image export failed.)"
                                if selected_backend == "local"
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
                # Serialise the element into markdown text (The element can be a text block, a picture, or a table etc.)
                serialized_text = serializer.serialize(item=element).text.strip()
            except Exception as exc:
                prefix = "local " if selected_backend == "local" else ""
                warnings.append(
                    f"Failed to serialize {prefix}element on page {page_no}: {exc}"
                )
                continue

            # Inject the picture markdown placeholder 
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

                # Submit any pending table-image VLM jobs after adding a new block
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
            print("[docling-pipeline] finalizing queued table-image VLM jobs...")
            # Submit the final batch of pending table-image VLM jobs
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

    print("[docling-pipeline] item processing complete")

    if not markdown_parts:
        #TODO: I saw a lot of validation needed to do for based on the backend to show the error
        if selected_backend == "local":
            raise RuntimeError(
                "No pages converted successfully with local Docling. "
                "Try Beam backend or a smaller local chunk size."
            )
        raise RuntimeError(
            "No markdown text serialized from Beam Docling endpoint response."
        )

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
        print("[docling-pipeline] writing markdown and manifest artifacts...")
        markdown_path.write_text(markdown_text, encoding="utf-8")

    structured_blocks = markdown_builder.build_structured_blocks(
        structured_block_metadata=structured_block_metadata,
        markdown_parts=markdown_parts,
    )

    stats = DoclingParseStats(
        converted_chunks=converted_chunks,
        partial_failure_chunks=len(partial_failures),
        pictures_extracted=sum(1 for item in images if item.kind == "picture"),
        table_fallback_images_extracted=table_image_count,
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
        structured_blocks=structured_blocks,
    )

    if artifacts_enabled and artifact_dir is not None:
        local_artifacts_store.write_manifest(artifact_dir, result_model.model_dump())

    print(
        "[docling-pipeline] file=%s backend=%s run_id=%s chunks=%s pictures=%s "
        "table_fallbacks=%s partial_failures=%s s3_uploaded=%s s3_failed=%s s3_skipped=%s"
        % (
            file_name,
            selected_backend,
            run_id,
            converted_chunks,
            stats.pictures_extracted,
            stats.table_fallback_images_extracted,
            stats.partial_failure_chunks,
            s3_upload_uploaded_count,
            s3_upload_failed_count,
            s3_upload_skipped_count,
        )
    )
    return result_model
