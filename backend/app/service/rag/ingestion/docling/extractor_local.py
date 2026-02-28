import io
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from uuid6 import uuid6

from app.service.rag.ingestion.docling import common as shared
from app.service.rag.ingestion.docling import table_image_vlm

# Configuration constants for local Docling processing.
# These can be tuned for performance, for Yoong Shen's machine, we are using this
LOCAL_DOCLING_CHUNK_SIZE = 6
LOCAL_DOCLING_LAYOUT_BATCH_SIZE = 16
LOCAL_DOCLING_TABLE_BATCH_SIZE = 16

_LOCAL_CONVERTER = None
_LOCAL_CONVERTER_LOCK = threading.Lock()


def _load_local_docling_runtime() -> dict[str, Any]:
    """
    Lazy-load local Docling classes to avoid import cost unless local backend is used.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        AcceleratorOptions,
        ThreadedPdfPipelineOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.transforms.serializer.markdown import MarkdownDocSerializer
    from docling_core.types.doc import (
        ListItem,
        PictureItem,
        SectionHeaderItem,
        TableItem,
        TitleItem,
    )
    from docling_core.types.io import DocumentStream

    return {
        "InputFormat": InputFormat,
        "AcceleratorOptions": AcceleratorOptions,
        "ThreadedPdfPipelineOptions": ThreadedPdfPipelineOptions,
        "DocumentConverter": DocumentConverter,
        "PdfFormatOption": PdfFormatOption,
        "MarkdownDocSerializer": MarkdownDocSerializer,
        "ListItem": ListItem,
        "PictureItem": PictureItem,
        "SectionHeaderItem": SectionHeaderItem,
        "TableItem": TableItem,
        "TitleItem": TitleItem,
        "DocumentStream": DocumentStream,
    }


def _build_local_converter() -> Any:
    """
    Build the local Docling converter with the requested low-memory CUDA config.
    """
    runtime = _load_local_docling_runtime()
    pipeline_options = runtime["ThreadedPdfPipelineOptions"]()
    pipeline_options.accelerator_options = runtime["AcceleratorOptions"](
        device="cuda", # If no cuda then just use cpu, but will defintely take longer
        num_threads=8, # Adjust this based on your own machine threads. Better to be threads = CPU cores
        cuda_use_flash_attention2=False,
    )

    pipeline_options.do_ocr = False
    pipeline_options.do_table_structure = True

    pipeline_options.generate_table_images = True
    pipeline_options.generate_picture_images = True
    pipeline_options.images_scale = 2.5
    pipeline_options.generate_page_images = False
    pipeline_options.do_chart_extraction = False
    pipeline_options.do_formula_enrichment = False

    pipeline_options.layout_batch_size = LOCAL_DOCLING_LAYOUT_BATCH_SIZE
    pipeline_options.table_batch_size = LOCAL_DOCLING_TABLE_BATCH_SIZE

    converter = runtime["DocumentConverter"](
        format_options={
            runtime["InputFormat"].PDF: runtime["PdfFormatOption"](
                pipeline_options=pipeline_options
            )
        }
    )

    print(
        f"Docling converter initialized with chunk_size={LOCAL_DOCLING_CHUNK_SIZE}."
    )
    print(
        f"Accelerator device: {pipeline_options.accelerator_options.device}"
    )
    return converter


def _get_or_create_local_converter() -> Any:
    """
    Return a cached local Docling converter instance.
    Else, build a new one and cache it for future use.
    """
    global _LOCAL_CONVERTER

    if _LOCAL_CONVERTER is not None:
        return _LOCAL_CONVERTER

    with _LOCAL_CONVERTER_LOCK:
        if _LOCAL_CONVERTER is None:
            _LOCAL_CONVERTER = _build_local_converter()
        return _LOCAL_CONVERTER


def _normalize_status(status: Any) -> str:
    """
    Normalize the status value from the Docling conversion result to a lowercase string.
    """
    value = getattr(status, "value", status)
    return str(value or "").strip().lower()


def _collect_result_errors(result: Any, *, fallback: str) -> list[str]:
    """
    Collect and stringify errors from the Docling conversion result.
    """
    raw_errors = getattr(result, "errors", None) or []
    errors = [shared._stringify_endpoint_error(err) for err in raw_errors if err is not None]
    return errors or [fallback]


def parse_pdf_with_docling_preview_local(
    pdf_bytes: bytes,
    file_name: str,
    artifact_root: Path | None = None,
    page_chunk_size: int = shared.DEFAULT_DOCLING_PAGE_CHUNK_SIZE,
) -> shared.DoclingParseResult:
    """
    Local Docling PDF preview pipeline: convert + serialize locally in fixed page chunks.
    """
    effective_page_chunk_size = (
        page_chunk_size
        if isinstance(page_chunk_size, int) and page_chunk_size > 0
        else LOCAL_DOCLING_CHUNK_SIZE
    )

    if not pdf_bytes:
        raise ValueError("empty pdf payload")

    runtime = _load_local_docling_runtime()
    converter = _get_or_create_local_converter()

    # Get the artifact directory for storing all the artifacts of the docling run
    run_id, artifact_dir, markdown_path = shared._prepare_docling_preview_artifact_dir(
        file_name=file_name,
        artifact_root=artifact_root,
    )
    safe_stem = shared._safe_stem(file_name)

    markdown_parts: list[str] = []
    structured_block_metadata: list[dict[str, Any]] = []
    images: list[shared.ExtractedImageArtifact] = []
    warnings: list[str] = []
    partial_failures: list[shared.DoclingChunkFailure] = []
    table_image_vlm_jobs: list[table_image_vlm.TableImageVlmJob] = []

    s3_upload_failed_count = 0
    s3_upload_uploaded_count = 0
    s3_upload_skipped_count = 0

    picture_counter = 0
    table_counter = 0
    table_image_count = 0
    converted_chunks = 0

    picture_item_cls = runtime["PictureItem"]
    table_item_cls = runtime["TableItem"]
    list_item_cls = runtime.get("ListItem")
    section_header_item_cls = runtime.get("SectionHeaderItem")
    title_item_cls = runtime.get("TitleItem")
    markdown_serializer_cls = runtime["MarkdownDocSerializer"]
    document_stream_cls = runtime["DocumentStream"]
    discovered_total_pages: int | None = None

    def _block_type_for_element(element: Any) -> str:
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

    def _append_markdown_block(
        *,
        text: str,
        block_type: str,
        page_no: int | None,
        is_table_image: bool = False,
        table_image_uuid: str | None = None,
    ) -> None:
        """Append markdown text and matching structured metadata in lockstep."""

        markdown_parts.append(text)
        structured_block_metadata.append(
            {
                "block_index": len(markdown_parts) - 1,
                "block_type": block_type,
                "page_no": page_no,
                "is_table_image": is_table_image,
                "table_image_uuid": table_image_uuid,
            }
        )
    
    # Build the shared VLM runtime if the VLM is enabled for this run
    table_image_vlm_runtime = table_image_vlm.build_table_image_vlm_runtime(
        artifact_dir=artifact_dir,
        warnings=warnings,
    )
    table_image_vlm_executor: ThreadPoolExecutor | None = None

    # ThreadPoolExecutor to process table image VLM jobs in parallel
    if table_image_vlm_runtime is not None:
        table_image_vlm_executor = ThreadPoolExecutor(
            max_workers=table_image_vlm_runtime.max_workers,
            thread_name_prefix="table-vlm",
        )

    current_start = 1

    # Loop over the PDF in chunks of pages
    while True:
        current_end = (
            min(current_start + effective_page_chunk_size - 1, discovered_total_pages)
            if discovered_total_pages is not None
            else current_start + effective_page_chunk_size - 1
        )
        page_range_label = f"{current_start}-{current_end}"
        print(f"[docling-local] Converting pages {page_range_label} ...")

        try:
            doc_stream = document_stream_cls(name=file_name, stream=io.BytesIO(pdf_bytes))

            # Convert the file chunk with Docling
            result = converter.convert(
                doc_stream,
                raises_on_error=False,
                page_range=(current_start, current_end),
            )
        except Exception as exc:
            partial_failures.append(
                shared.DoclingChunkFailure(
                    page_range=page_range_label,
                    errors=[str(exc)],
                )
            )
            warnings.append(f"Local Docling chunk {page_range_label} conversion exception: {exc}")
            if discovered_total_pages is None:
                break
            current_start += effective_page_chunk_size
            if current_start > discovered_total_pages:
                break
            continue

        result_input = getattr(result, "input", None)

        # Get the page count and update the total page
        result_page_count = getattr(result_input, "page_count", None)
        if (
            discovered_total_pages is None
            and isinstance(result_page_count, int)
            and result_page_count > 0
        ):
            discovered_total_pages = result_page_count

        # Validate the conversion status
        status_value = _normalize_status(getattr(result, "status", None))
        if status_value in {"failure", "skipped"}:
            errors = _collect_result_errors(
                result,
                fallback=f"Chunk conversion status={status_value}",
            )
            partial_failures.append(
                shared.DoclingChunkFailure(
                    page_range=page_range_label,
                    errors=errors,
                )
            )
            warnings.append(
                f"Local Docling chunk {page_range_label} did not convert successfully (status={status_value})."
            )

            if discovered_total_pages is None:
                break
            current_start += effective_page_chunk_size
            if current_start > discovered_total_pages:
                break
            continue

        converted_chunks += 1

        if status_value == "partial_success":
            partial_failures.append(
                shared.DoclingChunkFailure(
                    page_range=page_range_label,
                    errors=_collect_result_errors(
                        result,
                        fallback="Chunk returned partial_success with no errors list.",
                    ),
                )
            )

        serializer = markdown_serializer_cls(doc=result.document)

        # Loop through all the elements to determine it is a PictureItem or it's a tableItem
        for element, _level in result.document.iterate_items():
            page_no = shared._extract_page_no(element)
            picture_uuid_for_markdown: str | None = None

            # If its a pictureItem, then we will need to save it and uplaod it to S3
            if isinstance(element, picture_item_cls):
                picture_counter += 1
                picture_name = f"{safe_stem}-picture-{picture_counter}.png"
                picture_path = artifact_dir / picture_name
                image_uuid = str(uuid6())
                try:
                    img = element.get_image(result.document)
                    if img is None:
                        raise RuntimeError("Docling picture image unavailable.")
                    
                    # Save the image bytes to a local file in the artifact directory.
                    with picture_path.open("wb") as fp:
                        img.save(fp, "PNG")

                    # Form the Image Artifact for the picture item
                    image_artifact = shared.ExtractedImageArtifact(
                        kind="picture",
                        image_uuid=image_uuid,
                        file_name=picture_name,
                        file_path=str(picture_path),
                        page_no=page_no,
                        picture_index=picture_counter,
                    )

                    # Upload the picture image artifact to S3 and validate the upload status
                    image_artifact = shared._upload_image_artifact_to_s3(
                        image_artifact,
                        source_file_name=file_name,
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
                    picture_uuid_for_markdown = image_artifact.image_uuid
                except Exception as exc:
                    warnings.append(
                        f"Failed to export local picture #{picture_counter} on page {page_no}: {exc}"
                    )

            # If the element is a table we will perform the action below
            if isinstance(element, table_item_cls):
                table_counter += 1
                table_index = table_counter
                table_data = getattr(element, "data", None)
                num_rows = getattr(table_data, "num_rows", None)
                num_cols = getattr(table_data, "num_cols", None)

                if num_rows == 0 or num_cols == 0:
                    table_image_count += 1
                    table_image_name = f"{safe_stem}-table-{table_index}-{uuid6()}.png"
                    table_image_path = artifact_dir / table_image_name
                    image_uuid = str(uuid6())
                    try:
                        img = element.get_image(result.document)
                        if img is None:
                            raise RuntimeError("Docling table image unavailable.")
                        
                        # Save the image
                        with table_image_path.open("wb") as fp:
                            img.save(fp, "PNG")

                        # Form the image artifact for the tableItem
                        image_artifact = shared.ExtractedImageArtifact(
                            kind="table_image",
                            image_uuid=image_uuid,
                            file_name=table_image_name,
                            file_path=str(table_image_path),
                            page_no=page_no,
                            table_index=table_index,
                            reason="table_rows_cols_zero",
                        )

                        # Upload it to the S3 and validate the upload status for the table image artifact
                        image_artifact = shared._upload_image_artifact_to_s3(
                            image_artifact,
                            source_file_name=file_name,
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

                        table_markdown_lines = [
                            "> **Table (image)**: Table exists in image form.",
                            f"> {shared._table_image_uuid_marker(image_artifact.image_uuid)}",
                            f"> ![{table_image_name}]({table_image_name})",
                        ]

                        if table_image_vlm_runtime is not None and table_image_vlm_executor is not None:

                            # Get a summary placeholder for the table image VLM to inject into the markdown while the VLM job is being processed, 
                            # so that the markdown can be rendered with partial information while waiting for the VLM results
                            summary_placeholder = table_image_vlm.table_image_vlm_summary_placeholder(
                                image_artifact.image_uuid
                            )

                            # Form a VLM job to process this table image
                            table_image_vlm_jobs.append(
                                table_image_vlm.TableImageVlmJob(
                                    image_artifact=image_artifact,
                                    table_index=table_index,
                                    page_no=page_no,
                                    block_index=len(markdown_parts),
                                    summary_placeholder=summary_placeholder,
                                    # The output artifact directory for the VLM to write the extracted table structure JSON and the summary
                                    output_dir=table_image_vlm.table_image_vlm_output_dir(
                                        artifact_dir,
                                        table_index=table_index,
                                        image_uuid=image_artifact.image_uuid,
                                    ),
                                    # A JSON file path for debug
                                    json_rel_path=table_image_vlm.table_image_vlm_json_rel_path(
                                        table_index=table_index,
                                        image_uuid=image_artifact.image_uuid,
                                    ),
                                )
                            )
                            table_markdown_lines.append(f"> {summary_placeholder}")

                        # Append the markdown for this table item
                        _append_markdown_block(
                            text="\n".join(table_markdown_lines),
                            block_type="table",
                            page_no=page_no,
                            is_table_image=True,
                            table_image_uuid=image_artifact.image_uuid,
                        )

                        # Submit the VLM job
                        table_image_vlm.submit_ready_table_image_vlm_jobs(
                            runtime=table_image_vlm_runtime,
                            executor=table_image_vlm_executor,
                            jobs=table_image_vlm_jobs,
                            markdown_parts=markdown_parts,
                            warnings=warnings,
                        )
                    except Exception as exc:
                        warnings.append(
                            f"Failed to export local fallback table image #{table_index} on page {page_no}: {exc}"
                        )
                        _append_markdown_block(
                            text="\n".join(
                                [
                                    "> **Table (image)**: Table exists in image form.",
                                    "> (Local image export failed.)",
                                ]
                            ),
                            block_type="table",
                            page_no=page_no,
                            is_table_image=True,
                        )
                    continue

            # Serialise everything into markdown text
            try:
                serialized_text = serializer.serialize(item=element).text.strip()
            except Exception as exc:
                warnings.append(
                    f"Failed to serialize local element on page {page_no}: {exc}"
                )
                continue

            # If it's a picture item, we will inject a marker for the picture in the markdown text
            if isinstance(element, picture_item_cls):
                marker = (
                    shared._picture_uuid_marker(picture_uuid_for_markdown)
                    if picture_uuid_for_markdown
                    else shared.DOCLING_IMAGE_CROP_FAILED_MARKER
                )
                serialized_text = shared._inject_marker_for_picture(serialized_text, marker)

            if serialized_text:
                _append_markdown_block(
                    text=serialized_text,
                    block_type=_block_type_for_element(element),
                    page_no=page_no,
                    is_table_image=False,
                )

                # After each of the serialised text, we submit the job again
                # This is such that the after context for the VLM job can be gathered
                table_image_vlm.submit_ready_table_image_vlm_jobs(
                    runtime=table_image_vlm_runtime,
                    executor=table_image_vlm_executor,
                    jobs=table_image_vlm_jobs,
                    markdown_parts=markdown_parts,
                    warnings=warnings,
                )

        if discovered_total_pages is not None and current_end >= discovered_total_pages:
            break

        current_start += effective_page_chunk_size

    if table_image_vlm_executor is not None:
        # This is a final submission to process any remaining VLM jobs that have not been submitted
        # And they will not have any after context as the markdown is fully generated at this point
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
        table_image_vlm_executor.shutdown(wait=True)

    if not markdown_parts:
        raise RuntimeError(
            "No pages converted successfully with local Docling. Try Beam backend or a smaller local chunk size."
        )

    print("Successfully converted PDF with local Docling, writing markdown and artifacts")
    markdown_text = "\n\n".join(markdown_parts)
    markdown_path.write_text(markdown_text, encoding="utf-8")
    structured_blocks = [
        shared.DoclingStructuredBlock(
            block_index=metadata["block_index"],
            block_type=metadata["block_type"],
            content=markdown_parts[metadata["block_index"]],
            page_no=metadata["page_no"],
            is_table_image=metadata["is_table_image"],
            table_image_uuid=metadata["table_image_uuid"],
        )
        for metadata in structured_block_metadata
        if metadata["block_index"] < len(markdown_parts)
    ]

    # Form the stats for this docling extraction
    stats = shared.DoclingParseStats(
        converted_chunks=converted_chunks,
        partial_failure_chunks=len(partial_failures),
        pictures_extracted=sum(1 for item in images if item.kind == "picture"),
        table_fallback_images_extracted=table_image_count,
    )

    # Form the parse result for this docling extraction
    result_model = shared.DoclingParseResult(
        source_file_name=file_name,
        artifact_run_id=run_id,
        artifact_dir=str(artifact_dir),
        markdown_path=str(markdown_path),
        markdown_text=markdown_text,
        images=images,
        warnings=warnings,
        partial_failures=partial_failures,
        stats=stats,
        structured_blocks=structured_blocks,
    )

    # Write it into a manifest file for traceability, debugging and testing purposes
    shared._write_manifest(artifact_dir, result_model.model_dump())
    print(
        "[docling-preview-local] file=%s run_id=%s chunks=%s pictures=%s table_fallbacks=%s partial_failures=%s s3_uploaded=%s s3_failed=%s s3_skipped=%s"
        % (
            file_name,
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


def parse_pdf_with_docling_preview(*args: Any, **kwargs: Any) -> shared.DoclingParseResult:
    """
    Alias for direct module use; facade should call this provider-specific implementation.
    """
    return parse_pdf_with_docling_preview_local(*args, **kwargs)
