import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import fitz
import requests
from uuid6 import uuid6
from app.service.rag.ingestion.docling import common
from app.service.rag.ingestion.docling import table_image_vlm


# Re-export shared models/constants/helpers for compatibility while keeping implementations in docling_common.py.
DEFAULT_DOCLING_PAGE_CHUNK_SIZE = common.DEFAULT_DOCLING_PAGE_CHUNK_SIZE
DOCLING_IMAGE_PLACEHOLDER = common.DOCLING_IMAGE_PLACEHOLDER
DOCLING_IMAGE_CROP_FAILED_MARKER = common.DOCLING_IMAGE_CROP_FAILED_MARKER

ExtractedImageArtifact = common.ExtractedImageArtifact
DoclingChunkFailure = common.DoclingChunkFailure
DoclingParseStats = common.DoclingParseStats
DoclingParseResult = common.DoclingParseResult

_backend_root = common._backend_root
_default_preview_root = common._default_preview_root
_prepare_docling_preview_artifact_dir = common._prepare_docling_preview_artifact_dir
_image_file_name_from_uuid = common._image_file_name_from_uuid
_image_file_path_from_uuid = common._image_file_path_from_uuid
_image_markdown_rel_path_from_uuid = common._image_markdown_rel_path_from_uuid
_extract_page_no = common._extract_page_no
_picture_uuid_marker = common._picture_uuid_marker
_table_image_uuid_marker = common._table_image_uuid_marker
_inject_marker_for_picture = common._inject_marker_for_picture
_stringify_endpoint_error = common._stringify_endpoint_error
_upload_image_artifact_to_s3 = common._upload_image_artifact_to_s3
_write_manifest = common._write_manifest


BEAM_DOCLING_TIMEOUT_SECONDS = 600
BEAM_DOCLING_CLIENT_MAX_FILE_SIZE_MB = 25
BEAM_DOCLING_CLIENT_CROP_SCALE = 2.5
TABLE_IMAGE_VLM_OUTPUT_DIRNAME = table_image_vlm.TABLE_IMAGE_VLM_OUTPUT_DIRNAME
_TableImageVlmJob = table_image_vlm.TableImageVlmJob
_build_table_image_vlm_runtime = table_image_vlm.build_table_image_vlm_runtime
_submit_ready_table_image_vlm_jobs = table_image_vlm.submit_ready_table_image_vlm_jobs
_finalize_table_image_vlm_jobs = table_image_vlm.finalize_table_image_vlm_jobs
_table_image_vlm_summary_placeholder = table_image_vlm.table_image_vlm_summary_placeholder
_table_image_vlm_output_dir = table_image_vlm.table_image_vlm_output_dir
_table_image_vlm_json_rel_path = table_image_vlm.table_image_vlm_json_rel_path


def _load_docling_module_runtime() -> dict[str, Any]:
    """
    Lazy import only the Docling client-side types needed to reconstruct endpoint JSON
    and serialize markdown locally.
    """
    from docling_core.transforms.serializer.markdown import MarkdownDocSerializer
    from docling_core.types.doc import (
        DoclingDocument,
        ListItem,
        PictureItem,
        SectionHeaderItem,
        TableItem,
        TitleItem,
    )

    return {
        "MarkdownDocSerializer": MarkdownDocSerializer,
        "DoclingDocument": DoclingDocument,
        "ListItem": ListItem,
        "PictureItem": PictureItem,
        "SectionHeaderItem": SectionHeaderItem,
        "TableItem": TableItem,
        "TitleItem": TitleItem,
    }


def _load_beam_docling_config() -> dict[str, Any]:
    """
    Load required Beam Docling endpoint configuration from environment variables and return it as a dictionary.
    """
    endpoint = (os.getenv("BEAM_DOCLING_ENDPOINT") or "").strip()
    token = (os.getenv("BEAM_DOCLING_ENDPOINT_TOKEN") or "").strip()
    if not endpoint:
        raise RuntimeError("BEAM_DOCLING_ENDPOINT is not configured.")
    if not token:
        raise RuntimeError("BEAM_DOCLING_ENDPOINT_TOKEN is not configured.")
    return {
        "endpoint": endpoint,
        "token": token,
        "timeout_seconds": BEAM_DOCLING_TIMEOUT_SECONDS,
        "max_file_size_mb": BEAM_DOCLING_CLIENT_MAX_FILE_SIZE_MB,
    }


def _extract_document_dump(result: dict[str, Any]) -> dict[str, Any] | None:
    """
    Read Docling document JSON from either the new `document_dump` field or
    the legacy `conversion_result_dump.document` field.
    """
    document_dump = result.get("document_dump")
    if isinstance(document_dump, dict):
        return document_dump

    conversion_result_dump = result.get("conversion_result_dump")
    if isinstance(conversion_result_dump, dict):
        nested_document = conversion_result_dump.get("document")
        if isinstance(nested_document, dict):
            return nested_document

    return None


def _parse_beam_response_json(response: requests.Response, raw_body: str) -> dict[str, Any]:
    """
    Parse Beam response JSON defensively to tolerate parser differences and
    occasional trailing non-JSON content in otherwise valid responses.
    """
    parse_errors: list[str] = []

    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            return parsed
        parse_errors.append(
            f"response.json() returned {type(parsed).__name__}, expected object"
        )
    except Exception as exc:
        parse_errors.append(f"response.json(): {type(exc).__name__}: {exc}")

    try:
        parsed = json.loads(raw_body)
        if isinstance(parsed, dict):
            return parsed
        parse_errors.append(
            f"json.loads(raw_body) returned {type(parsed).__name__}, expected object"
        )
    except Exception as exc:
        parse_errors.append(f"json.loads(raw_body): {type(exc).__name__}: {exc}")

    trimmed = raw_body.lstrip("\ufeff\r\n\t ")
    try:
        decoder = json.JSONDecoder()
        parsed, end_idx = decoder.raw_decode(trimmed)
        trailing = trimmed[end_idx:].strip()
        if isinstance(parsed, dict):
            if trailing:
                print(
                    "[docling-preview] Beam response contained trailing text after JSON payload; trailing bytes were ignored."
                )
            return parsed
        parse_errors.append(
            f"JSONDecoder.raw_decode returned {type(parsed).__name__}, expected object"
        )
    except Exception as exc:
        parse_errors.append(f"JSONDecoder.raw_decode(): {type(exc).__name__}: {exc}")

    body_preview = raw_body[:1000]
    raise RuntimeError(
        "Beam Docling endpoint returned non-JSON response. "
        f"status={response.status_code}, content_type={response.headers.get('Content-Type', '<empty>')!r}, "
        f"decode_errors={' | '.join(parse_errors)}, body_preview={body_preview!r}"
    )


def _call_beam_docling_endpoint(pdf_bytes: bytes, file_name: str) -> dict[str, Any]:
    """
    Call the Beam-hosted Docling endpoint and return the parsed JSON response.
    """
    config = _load_beam_docling_config() # Get the endpoint configuration for the request
    encoded_pdf = base64.b64encode(pdf_bytes).decode("ascii")

    payload = {
        "filename": file_name,
        "file_b64": encoded_pdf,
        "include_conversion_dump": False,
        "include_document_dump": True,
        "include_item_dump": False,
        "max_file_size_mb": config["max_file_size_mb"],
    }
    headers = {
        "Authorization": f"Bearer {config['token']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    print("Sending PDF document to Beam Docling endpoint for conversion...")
    try:
        response = requests.post(
            config["endpoint"],
            json=payload,
            headers=headers,
            timeout=config["timeout_seconds"],
        )
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Beam Docling endpoint request failed: {exc}"
        ) from exc

    raw_body = response.text or ""
    if not raw_body.strip():
        raise RuntimeError("Beam Docling endpoint returned empty response body.")

    # Raise error if the response status code indicates failure
    if not response.ok:
        body_preview = raw_body[:1000]
        raise RuntimeError(
            "Beam Docling endpoint returned HTTP error: "
            f"status={response.status_code}, body_preview={body_preview!r}"
        )

    result = _parse_beam_response_json(response, raw_body)

    if result.get("ok") is False:
        error_code = result.get("error_code") or "UNKNOWN"
        error_message = result.get("error_message") or "Beam endpoint error"
        raise RuntimeError(
            "Beam Docling endpoint returned error response: "
            f"code={error_code}, message={error_message}"
        )

    document_dump = _extract_document_dump(result)
    if not isinstance(document_dump, dict):
        raise RuntimeError(
            "Beam Docling endpoint response missing document_dump (and legacy conversion_result_dump.document fallback)."
        )

    print("Successfully received response from Beam Docling endpoint.")
    return result


# Backward-compatible alias used by older tests/helpers.
_load_docling_runtime = _load_docling_module_runtime


def _ordered_items_by_seq(ordered_items: Any) -> dict[int, dict[str, Any]]:
    """
    Build a sequence-indexed lookup map for endpoint ordered items.
    """

    mapped: dict[int, dict[str, Any]] = {}
    if not isinstance(ordered_items, list):
        return mapped
    # Loop through the ordered items from the endpoint response and build a mapping of sequence numbers to the corresponding items. 
    # This allows for quick lookup of items by their sequence number during the markdown serialization process.
    for item in ordered_items:
        if not isinstance(item, dict):
            continue
        seq = item.get("seq")
        if isinstance(seq, int):
            mapped[seq] = item
    return mapped


def _coerce_endpoint_table_shape(endpoint_item: dict[str, Any]) -> tuple[int | None, int | None]:
    """
    Extract table row/column counts from endpoint metadata when present.
    """
    table_info = endpoint_item.get("table_info")
    if not isinstance(table_info, dict):
        return None, None
    num_rows = table_info.get("num_rows")
    num_cols = table_info.get("num_cols")
    return (
        num_rows if isinstance(num_rows, int) else None,
        num_cols if isinstance(num_cols, int) else None,
    )


def _crop_image_bytes_from_endpoint_item(
    endpoint_item: dict[str, Any],
    pdf_doc: fitz.Document,
    *,
    scale: float = BEAM_DOCLING_CLIENT_CROP_SCALE,
) -> bytes | None:
    """
    Crop an image region from the source PDF using endpoint-provided `page_no` + `bbox`.
    """
    if not isinstance(endpoint_item, dict):
        return None

    bbox = endpoint_item.get("bbox")
    page_no = endpoint_item.get("page_no")
    if not isinstance(bbox, dict) or not isinstance(page_no, int):
        return None
    if page_no <= 0 or page_no > len(pdf_doc):
        return None

    try:
        left_raw = float(bbox["l"])
        top_raw = float(bbox["t"])
        right_raw = float(bbox["r"])
        bottom_raw = float(bbox["b"])
    except Exception:
        return None

    page = pdf_doc.load_page(page_no - 1)
    page_rect = page.rect
    coord_origin = str(bbox.get("coord_origin", "TOPLEFT")).upper()

    if coord_origin == "BOTTOMLEFT":
        y1 = page_rect.height - top_raw
        y2 = page_rect.height - bottom_raw
    else:
        y1 = top_raw
        y2 = bottom_raw

    clip = fitz.Rect(
        min(left_raw, right_raw),
        min(y1, y2),
        max(left_raw, right_raw),
        max(y1, y2),
    )
    clip = clip & page_rect
    if clip.is_empty or clip.width <= 0 or clip.height <= 0:
        return None

    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    if pix.width <= 0 or pix.height <= 0:
        return None
    return pix.tobytes("png")


def parse_pdf_with_docling_preview(
    pdf_bytes: bytes,
    file_name: str,
    artifact_root: Path | None = None,
) -> DoclingParseResult:
    """
    Parse a PDF with Docling and persist preview artifacts (markdown + extracted images).
    """
    if not pdf_bytes:
        raise ValueError("empty pdf payload")

    endpoint_result = _call_beam_docling_endpoint(pdf_bytes, file_name) # Call the Beam-hosted Docling endpoint and get the conversion result
    runtime = _load_docling_module_runtime() # Load the runtime mmodules from docling for serialising

    # Preparation for the preview artifact directory and markdown output path
    run_id, artifact_dir, markdown_path = _prepare_docling_preview_artifact_dir(
        file_name=file_name,
        artifact_root=artifact_root,
    )

    markdown_parts: list[str] = [] # A list to accumulate serialised markdown text parts
    structured_block_metadata: list[dict[str, Any]] = []
    images: list[ExtractedImageArtifact] = [] # A list to hold metadata about extracted images
    warnings: list[str] = []
    partial_failures: list[DoclingChunkFailure] = []
    s3_upload_failed_count = 0
    s3_upload_uploaded_count = 0
    s3_upload_skipped_count = 0
    table_image_vlm_jobs: list[_TableImageVlmJob] = []

    picture_counter = 0 # For tracking the number of pictures extracted, used in naming the picture artifacts
    table_counter = 0 # For tracking the number of tables processed, used in naming extracted table image artifacts
    table_image_count = 0 # For tracking the number of extracted table images used in stats and logging

    table_image_vlm_runtime = _build_table_image_vlm_runtime(
        artifact_dir=artifact_dir,
        warnings=warnings,
    )
    table_image_vlm_executor: ThreadPoolExecutor | None = None
    if table_image_vlm_runtime is not None:
        table_image_vlm_executor = ThreadPoolExecutor(
            max_workers=table_image_vlm_runtime.max_workers,
            thread_name_prefix="table-vlm",
        )

    # Extract all the server warnings and add it to warnings list for debugging purpose
    for note in endpoint_result.get("server_notes") or []:
        if note:
            warnings.append(f"Beam: {note}")

    endpoint_status = str(endpoint_result.get("status") or "")
    endpoint_errors = endpoint_result.get("errors") or []
    if endpoint_status == "partial_success" and endpoint_errors:
        partial_failures.append(
            DoclingChunkFailure(
                page_range="full-document",
                errors=[_stringify_endpoint_error(err) for err in endpoint_errors],
            )
        )

    doc_dump = _extract_document_dump(endpoint_result)
    if not isinstance(doc_dump, dict):
        raise RuntimeError(
            "Beam Docling endpoint response missing document_dump (and legacy conversion_result_dump.document fallback)."
        )

    # Reconstruct the DoclingDocument from the conversion result dump for local serialization using the Docling own class.
    doc = runtime["DoclingDocument"].model_validate(doc_dump)

    serializer = runtime["MarkdownDocSerializer"](doc=doc)
    picture_item_cls = runtime["PictureItem"]
    table_item_cls = runtime["TableItem"]
    list_item_cls = runtime.get("ListItem")
    section_header_item_cls = runtime.get("SectionHeaderItem")
    title_item_cls = runtime.get("TitleItem")
    ordered_by_seq = _ordered_items_by_seq(endpoint_result.get("ordered_items")) # A list of items in the order they were parsed by the endpoint, with sequence numbers for reference.

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

    print("Starting to process the converted Docling document and generate markdown and image artifacts")
    # Open the PDF with fitz to enable cropping of image regions for pictures and tables based on endpoint-provided bounding boxes.
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf_doc:
            for seq, (element, _) in enumerate(doc.iterate_items()):
                # Loop through each of the elements
                endpoint_item = ordered_by_seq.get(seq, {})

                # Get page number for the current element
                page_no = (
                    endpoint_item.get("page_no")
                    if isinstance(endpoint_item, dict) and isinstance(endpoint_item.get("page_no"), int)
                    else _extract_page_no(element)
                )
                is_picture_item = isinstance(element, picture_item_cls)
                is_table_item = isinstance(element, table_item_cls)
                picture_markdown_placeholder: str | None = None

                # Process pictureItems by cropping and optionally uploading them.
                if is_picture_item:
                    picture_markdown_placeholder = DOCLING_IMAGE_CROP_FAILED_MARKER
                    picture_counter += 1
                    image_uuid = str(uuid6())
                    picture_name = _image_file_name_from_uuid(image_uuid)
                    picture_path = _image_file_path_from_uuid(artifact_dir, image_uuid)
                    try:
                        png_bytes = _crop_image_bytes_from_endpoint_item(endpoint_item, pdf_doc)
                        if not png_bytes:
                            raise RuntimeError("missing/invalid bbox or crop produced no pixels")

                        picture_path.write_bytes(png_bytes)

                        image_artifact = ExtractedImageArtifact(
                            kind="picture",
                            image_uuid=image_uuid,
                            file_name=picture_name,
                            file_path=str(picture_path),
                            page_no=page_no,
                            picture_index=picture_counter,
                        )

                        image_artifact = _upload_image_artifact_to_s3(
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
                        picture_markdown_placeholder = _picture_uuid_marker(
                            image_artifact.image_uuid
                        )
                    except Exception as exc:
                        warnings.append(
                            f"Failed to export picture #{picture_counter} on page {page_no}: {exc}"
                        )

                # Process table items that only exist as images (Docling table rows/cols == 0).
                if is_table_item:
                    table_counter += 1
                    table_index = table_counter

                    num_rows, num_cols = _coerce_endpoint_table_shape(endpoint_item)
                    if num_rows == 0 or num_cols == 0:
                        table_image_count += 1
                        image_uuid = str(uuid6())
                        table_image_name = _image_file_name_from_uuid(image_uuid)
                        table_image_path = _image_file_path_from_uuid(
                            artifact_dir,
                            image_uuid,
                        )
                        try:
                            png_bytes = _crop_image_bytes_from_endpoint_item(endpoint_item, pdf_doc)
                            if not png_bytes:
                                raise RuntimeError("missing/invalid bbox or crop produced no pixels")

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

                            image_artifact = _upload_image_artifact_to_s3(
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
                                f"> {_table_image_uuid_marker(image_artifact.image_uuid)}",
                                f"> ![{table_image_name}]({_image_markdown_rel_path_from_uuid(image_artifact.image_uuid)})",
                            ]

                            if table_image_vlm_runtime is not None and table_image_vlm_executor is not None:
                                summary_placeholder = _table_image_vlm_summary_placeholder(
                                    image_artifact.image_uuid
                                )
                                table_image_vlm_jobs.append(
                                    _TableImageVlmJob(
                                        image_artifact=image_artifact,
                                        table_index=table_index,
                                        page_no=page_no,
                                        block_index=len(markdown_parts),
                                        summary_placeholder=summary_placeholder,
                                        output_dir=_table_image_vlm_output_dir(
                                            artifact_dir,
                                            table_index=table_index,
                                            image_uuid=image_artifact.image_uuid,
                                        ),
                                        json_rel_path=_table_image_vlm_json_rel_path(
                                            table_index=table_index,
                                            image_uuid=image_artifact.image_uuid,
                                        ),
                                    )
                                )
                                table_markdown_lines.append(f"> {summary_placeholder}")

                            _append_markdown_block(
                                text="\n".join(table_markdown_lines),
                                block_type="table",
                                page_no=page_no,
                                is_table_image=True,
                                table_image_uuid=image_artifact.image_uuid,
                            )
                            _submit_ready_table_image_vlm_jobs(
                                runtime=table_image_vlm_runtime,
                                executor=table_image_vlm_executor,
                                jobs=table_image_vlm_jobs,
                                markdown_parts=markdown_parts,
                                warnings=warnings,
                            )
                        except Exception as exc:
                            warnings.append(
                                f"Failed to export fallback table image #{table_index} on page {page_no}: {exc}"
                            )
                            _append_markdown_block(
                                text="\n".join(
                                    [
                                        "> **Table (image)**: Table exists in image form.",
                                        "> (Local crop failed: missing/invalid bbox or page_no.)",
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
                        f"Failed to serialize element on page {page_no}: {exc}"
                    )
                    continue

                # Replace the picture placeholder only after serialization to keep image
                # extraction/upload logic separate from markdown serialization.
                if picture_markdown_placeholder is not None:
                    serialized_text = _inject_marker_for_picture(
                        serialized_text, picture_markdown_placeholder
                    )

                if serialized_text:
                    _append_markdown_block(
                        text=serialized_text,
                        block_type=_block_type_for_element(element),
                        page_no=page_no,
                        is_table_image=False,
                    )
                    _submit_ready_table_image_vlm_jobs(
                        runtime=table_image_vlm_runtime,
                        executor=table_image_vlm_executor,
                        jobs=table_image_vlm_jobs,
                        markdown_parts=markdown_parts,
                        warnings=warnings,
                    )
    finally:
        if table_image_vlm_executor is not None:
            _submit_ready_table_image_vlm_jobs(
                runtime=table_image_vlm_runtime,
                executor=table_image_vlm_executor,
                jobs=table_image_vlm_jobs,
                markdown_parts=markdown_parts,
                warnings=warnings,
                force=True,
            )
            _finalize_table_image_vlm_jobs(
                artifact_dir=artifact_dir,
                jobs=table_image_vlm_jobs,
                markdown_parts=markdown_parts,
                warnings=warnings,
            )
            table_image_vlm_executor.shutdown(wait=True)

    if not markdown_parts:
        raise RuntimeError(
            "No markdown text serialized from Beam Docling endpoint response."
        )

    converted_chunks = 1
    print("Successfully extracted the PDF document using Beam endpoint")
    markdown_text = "\n\n".join(markdown_parts)
    markdown_path.write_text(markdown_text, encoding="utf-8") # Write the combined markdown text to the markdown file in the artifact directory
    structured_blocks = [
        common.DoclingStructuredBlock(
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

    # Forming the stats for debugging and traceability
    stats = DoclingParseStats(
        converted_chunks=converted_chunks,
        partial_failure_chunks=len(partial_failures),
        pictures_extracted=sum(1 for item in images if item.kind == "picture"),
        table_fallback_images_extracted=table_image_count,
    )

    # Form the final result model for debugging and traceability
    result_model = DoclingParseResult(
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

    # Write a manifest.json file in the artifact directory containing metadata about the Docling parsing result for debugging and traceability.
    _write_manifest(artifact_dir, result_model.model_dump())
    print(
        "[docling-preview] file=%s run_id=%s chunks=%s pictures=%s table_fallbacks=%s partial_failures=%s s3_uploaded=%s s3_failed=%s s3_skipped=%s"
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


def get_pdf_ingestion_strategy() -> str:
    """
    Determine the PDF ingestion strategy based on environment variable.
    """
    strategy = os.getenv("INGEST_PDF_EXTRACTOR", "legacy").strip().lower() or "legacy"
    return strategy if strategy in {"legacy", "docling"} else "legacy"
