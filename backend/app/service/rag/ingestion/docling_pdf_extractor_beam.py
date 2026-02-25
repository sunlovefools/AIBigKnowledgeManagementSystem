import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Literal

import fitz
import requests
from pydantic import BaseModel
from uuid6 import uuid6
from app.service.storage.s3_image_store import (
    build_s3_image_key,
    _load_s3_config,
    upload_file_to_s3,
)


DEFAULT_DOCLING_PAGE_CHUNK_SIZE = 6 # Default number of pages to process in each chunk when parsing PDFs with Docling.
BEAM_DOCLING_TIMEOUT_SECONDS = 600
BEAM_DOCLING_CLIENT_MAX_FILE_SIZE_MB = 25
BEAM_DOCLING_CLIENT_CROP_SCALE = 2.5
DOCLING_IMAGE_PLACEHOLDER = "<!-- image -->"
DOCLING_IMAGE_CROP_FAILED_MARKER = "<!-- image-crop-failed -->"


class ExtractedImageArtifact(BaseModel):
    """
    Represents an image extracted from a PDF page, either a regular picture or a table image.
    """
    kind: Literal["picture", "table_image"]
    image_uuid: str
    file_name: str
    file_path: str
    page_no: int | None = None
    table_index: int | None = None
    picture_index: int | None = None
    reason: str | None = None
    s3_bucket: str | None = None
    s3_key: str | None = None
    s3_region: str | None = None
    s3_uri: str | None = None
    s3_upload_status: Literal["uploaded", "failed", "skipped"] | None = None
    s3_error: str | None = None


class DoclingChunkFailure(BaseModel):
    """
    Represents a partial failure in converting a chunk of pages, including the page range and error details.
    """
    page_range: str
    errors: list[str]


class DoclingParseStats(BaseModel):
    """
    Statistics about the Docling parsing process, including chunking and extraction details.
    """
    converted_chunks: int
    partial_failure_chunks: int
    pictures_extracted: int
    table_fallback_images_extracted: int


class DoclingParseResult(BaseModel):
    """
    Represents the result of parsing a PDF with Docling, including paths to artifacts, extracted images, warnings, and stats.
    """
    source_file_name: str
    artifact_run_id: str
    artifact_dir: str
    markdown_path: str
    markdown_text: str
    images: list[ExtractedImageArtifact]
    warnings: list[str]
    partial_failures: list[DoclingChunkFailure]
    stats: DoclingParseStats


def _backend_root() -> Path:
    """
    Get the root directory of the backend project.
    """
    return Path(__file__).resolve().parents[4]


def _default_preview_root() -> Path:
    """
    Get the default directory for storing Docling preview artifacts.
    """
    return _backend_root() / "_local_uploads" / "docling_previews"


def _prepare_docling_preview_artifact_dir(
    *,
    file_name: str,
    artifact_root: Path | None = None,
) -> tuple[str, Path, Path]:
    """
    Prepare the per-run preview artifact directory and markdown output path.
    """
    preview_root = Path(artifact_root) if artifact_root else _default_preview_root()
    preview_root.mkdir(parents=True, exist_ok=True)

    run_id = str(uuid6())
    artifact_dir = preview_root / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    markdown_path = artifact_dir / "document.md"
    return run_id, artifact_dir, markdown_path


def _safe_stem(file_name: str) -> str:
    """
    Generate a safe stem for the given file name by removing unsafe characters and normalizing it.

    Stem is a simplified version of the file name without extension, used for naming extracted artifacts.
    Example of stem is: "my_document" for "my_document.pdf", or "report_2024" for "report 2024 (final).pdf".
    """
    stem = Path(file_name or "document.pdf").stem or "document"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem or "document"


def _extract_page_no(doc_item: Any) -> int | None:
    """
    Extract the page number from a Docling document item, if available.
    Docling document items may have a 'prov' attribute that contains provenance information, including page numbers.
    """
    prov = getattr(doc_item, "prov", None) or []
    if not prov:
        return None
    return getattr(prov[0], "page_no", None)


def _picture_uuid_marker(image_uuid: str) -> str:
    """Return the markdown comment marker used for extracted picture UUIDs."""
    return f"<!-- image-uuid: {image_uuid} -->"


def _table_image_uuid_marker(image_uuid: str) -> str:
    """Return the markdown comment marker used for fallback table image UUIDs."""
    return f"<!-- table-image-uuid: {image_uuid} -->"


def _load_docling_module_runtime() -> dict[str, Any]:
    """
    Lazy import only the Docling client-side types needed to reconstruct endpoint JSON
    and serialize markdown locally.
    """
    from docling_core.transforms.serializer.markdown import MarkdownDocSerializer
    from docling_core.types.doc import DoclingDocument
    from docling_core.types.doc import PictureItem, TableItem

    return {
        "MarkdownDocSerializer": MarkdownDocSerializer,
        "DoclingDocument": DoclingDocument,
        "PictureItem": PictureItem,
        "TableItem": TableItem,
    }


def _upload_image_artifact_to_s3(
    image_artifact: ExtractedImageArtifact,
    *,
    source_file_name: str,
) -> ExtractedImageArtifact:
    """
    Best-effort S3 upload for a locally saved image artifact.

    This function mutates and returns the image artifact with S3 metadata/status.
    """
    upload_enabled = (os.getenv("AWS_S3_UPLOAD_ENABLED", "false") or "").strip().lower()
    if upload_enabled != "true":
        image_artifact.s3_upload_status = "skipped"
        image_artifact.s3_error = "S3 upload disabled (AWS_S3_UPLOAD_ENABLED=false)"
        print(f"S3 upload skipped for image_uuid={image_artifact.image_uuid} because S3 uploads are disabled.")
        return image_artifact

    try:
        # Validate and load config at the caller to keep storage module upload functions focused.
        s3_config = _load_s3_config()
        if s3_config is None:
            image_artifact.s3_upload_status = "skipped"
            image_artifact.s3_error = "S3 upload disabled (missing config)"
            return image_artifact
        
        # Build the S3 key for the image artifact and attempt the upload. Update the artifact with the result.
        s3_key = build_s3_image_key(
            image_uuid=image_artifact.image_uuid,
            extension=Path(image_artifact.file_name).suffix or ".png",
            prefix=s3_config.prefix,
            source_file_name=source_file_name,
        )

        # Upload the image file to S3 with metadata for traceability, including the source PDF file name and page number if available.
        upload_result = upload_file_to_s3(
            local_path=image_artifact.file_path,
            key=s3_key,
            content_type="image/png",
            metadata={
                "image_uuid": image_artifact.image_uuid,
                "kind": image_artifact.kind,
                "source_file_name": source_file_name,
                "page_no": "" if image_artifact.page_no is None else str(image_artifact.page_no),
            },
            config=s3_config,
        )
        image_artifact.s3_bucket = upload_result.bucket
        image_artifact.s3_key = upload_result.key
        image_artifact.s3_region = upload_result.region
        image_artifact.s3_uri = upload_result.s3_uri
        image_artifact.s3_upload_status = "uploaded"
        image_artifact.s3_error = None
    except Exception as exc:
        image_artifact.s3_upload_status = "failed"
        image_artifact.s3_error = str(exc)
    return image_artifact


def _write_manifest(artifact_dir: Path, payload: dict[str, Any]) -> None:
    """
    Write a manifest.json file in the artifact directory containing metadata about the Docling parsing result.
    For the purpose of this is to for debugging and traceability
    """
    manifest_path = artifact_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


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


def _call_beam_docling_endpoint(pdf_bytes: bytes, file_name: str) -> dict[str, Any]:
    """
    Call the Beam-hosted Docling endpoint and return the parsed JSON response.
    """
    config = _load_beam_docling_config() # Get the endpoint configuration for the request
    payload = {
        "filename": file_name,
        "file_b64": base64.b64encode(pdf_bytes).decode("ascii"), # Encode the PDF bytes as a base64 string for transmission in JSON
        "include_conversion_dump": True, # Include the full conversion result dump in the response for richer debugging and preview generation, at the cost of larger response size.
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

    # Parse the response body as JSON
    try:
        result = response.json()
    except Exception as exc:
        body_preview = raw_body[:1000]
        raise RuntimeError(
            "Beam Docling endpoint returned non-JSON response. "
            f"status={response.status_code}, body_preview={body_preview!r}"
        ) from exc

    if isinstance(result, dict) and result.get("ok") is False:
        error_code = result.get("error_code") or "UNKNOWN"
        error_message = result.get("error_message") or "Beam endpoint error"
        raise RuntimeError(
            "Beam Docling endpoint returned error response: "
            f"code={error_code}, message={error_message}"
        )

    conversion_result_dump = result.get("conversion_result_dump")
    if not isinstance(conversion_result_dump, dict):
        raise RuntimeError("Beam Docling endpoint response missing conversion_result_dump.")

    # Making sure the conversion result dump contains a document entry, which is essential for the parsing process. If it's missing or not a dict, raise an error.
    if not isinstance(conversion_result_dump.get("document"), dict):
        raise RuntimeError("Beam Docling endpoint response missing conversion_result_dump.document.")

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


def _inject_marker_for_picture(serialized_text: str, marker: str) -> str:
    """
    Replace the Docling image placeholder with a marker, or append the marker if no placeholder exists.
    """
    text = (serialized_text or "").strip()
    if not text:
        return marker
    if DOCLING_IMAGE_PLACEHOLDER in text:
        return text.replace(DOCLING_IMAGE_PLACEHOLDER, marker).strip()
    return f"{text}\n\n{marker}"


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


def _stringify_endpoint_error(error: Any) -> str:
    """
    Convert endpoint error payload entries into readable strings for warnings/manifests.
    """
    if isinstance(error, str):
        return error
    try:
        return json.dumps(error, ensure_ascii=False)
    except Exception:
        return str(error)


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
    safe_stem = _safe_stem(file_name)

    markdown_parts: list[str] = [] # A list to accumulate serialised markdown text parts
    images: list[ExtractedImageArtifact] = [] # A list to hold metadata about extracted images
    warnings: list[str] = []
    partial_failures: list[DoclingChunkFailure] = []
    s3_upload_failed_count = 0
    s3_upload_uploaded_count = 0
    s3_upload_skipped_count = 0

    picture_counter = 0 # For tracking the number of pictures extracted, used in naming the picture artifacts
    table_counter = 0 # For tracking the number of tables processed, used in naming extracted table image artifacts
    table_image_count = 0 # For tracking the number of extracted table images used in stats and logging

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

    # Conversion_result_dump is the object for DoclingDocument but in the form of JSON
    conversion_result_dump = endpoint_result.get("conversion_result_dump") or {}
    doc_dump = conversion_result_dump.get("document")

    # Reconstruct the DoclingDocument from the conversion result dump for local serialization using the Docling own class.
    doc = runtime["DoclingDocument"].model_validate(doc_dump)

    serializer = runtime["MarkdownDocSerializer"](doc=doc)
    picture_item_cls = runtime["PictureItem"]
    table_item_cls = runtime["TableItem"]
    ordered_by_seq = _ordered_items_by_seq(endpoint_result.get("ordered_items")) # A list of items in the order they were parsed by the endpoint, with sequence numbers for reference.

    print("Starting to process the converted Docling document and generate markdown and image artifacts")
    # Open the PDF with fitz to enable cropping of image regions for pictures and tables based on endpoint-provided bounding boxes.
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

            # Proces pictureItems by cropping it, upload it to S3, and keep track of the metadata for markdown generation and stats.
            if is_picture_item:
                # Decide the markdown placeholder before serialization. If export fails,
                # we keep the crop-failed marker; if it succeeds, replace with UUID marker.
                picture_markdown_placeholder = DOCLING_IMAGE_CROP_FAILED_MARKER
                picture_counter += 1
                picture_name = f"{safe_stem}-picture-{picture_counter}.png"
                picture_path = artifact_dir / picture_name
                image_uuid = str(uuid6())
                try:
                    # Crop the image bytes from the source PDF using the bounding box and page number provided.
                    png_bytes = _crop_image_bytes_from_endpoint_item(endpoint_item, pdf_doc)
                    if not png_bytes:
                        raise RuntimeError("missing/invalid bbox or crop produced no pixels")
                    
                    # Save the cropped image bytes to a local file in the artifact directory.
                    picture_path.write_bytes(png_bytes)
                    
                    # Forming the image artifact 
                    image_artifact = ExtractedImageArtifact(
                        kind="picture",
                        image_uuid=image_uuid,
                        file_name=picture_name,
                        file_path=str(picture_path),
                        page_no=page_no,
                        picture_index=picture_counter,
                    )

                    # Upload the image artifact to S3
                    image_artifact = _upload_image_artifact_to_s3(
                        image_artifact,
                        source_file_name=file_name,
                    )

                    # Validation on the S3 upload status
                    if image_artifact.s3_upload_status == "failed":
                        s3_upload_failed_count += 1
                        warnings.append(
                            f"Failed to upload picture image_uuid={image_artifact.image_uuid} to S3: {image_artifact.s3_error}"
                        )
                    elif image_artifact.s3_upload_status == "uploaded":
                        s3_upload_uploaded_count += 1
                    elif image_artifact.s3_upload_status == "skipped":
                        s3_upload_skipped_count += 1

                    # Add the image artifact to the list of images for stats and markdown generation.
                    images.append(image_artifact)

                    # Get the markdown placeholder for the picture which will be injected into the serialised markdown text
                    picture_markdown_placeholder = _picture_uuid_marker(
                        image_artifact.image_uuid
                    )

                except Exception as exc:
                    warnings.append(
                        f"Failed to export picture #{picture_counter} on page {page_no}: {exc}"
                    )

            # Process tableItems by where only process tableItems that exist in the form of Images
            # We crop it, save it locally as artifact, upload it to s3 and keep track of the metadata for markdown generation and stats. 
            # The markdown will indicate that it's a table in image form with the corresponding image if crop and upload succeeded, or indicate crop failure if crop failed.
            if is_table_item:
                table_counter += 1
                table_index = table_counter
                
                # Get the number of rows and columns for the table to determine if we need to crop it out
                num_rows, num_cols = _coerce_endpoint_table_shape(endpoint_item)

                # If the table has no row and column, then it exist in the form of image
                if num_rows == 0 or num_cols == 0:
                    table_image_count += 1
                    table_image_name = f"{safe_stem}-table-{table_index}-{uuid6()}.png"
                    table_image_path = artifact_dir / table_image_name
                    image_uuid = str(uuid6())
                    try:
                        # Crop the table image out based on the bounding box and page number provided
                        png_bytes = _crop_image_bytes_from_endpoint_item(endpoint_item, pdf_doc)
                        if not png_bytes:
                            raise RuntimeError("missing/invalid bbox or crop produced no pixels")
                        
                        # Save the cropped table image bytes to a local file in the artifact directory.
                        table_image_path.write_bytes(png_bytes)

                        # Form the Image Artifact for the cropped table image
                        image_artifact = ExtractedImageArtifact(
                            kind="table_image",
                            image_uuid=image_uuid,
                            file_name=table_image_name,
                            file_path=str(table_image_path),
                            page_no=page_no,
                            table_index=table_index,
                            reason="table_rows_cols_zero",
                        )

                        # Upload the table image artifact to S3
                        image_artifact = _upload_image_artifact_to_s3(
                            image_artifact,
                            source_file_name=file_name,
                        )

                        # Validate the S3 upload status for the table image artifact
                        if image_artifact.s3_upload_status == "failed":
                            s3_upload_failed_count += 1
                            warnings.append(
                                f"Failed to upload table image image_uuid={image_artifact.image_uuid} to S3: {image_artifact.s3_error}"
                            )
                        elif image_artifact.s3_upload_status == "uploaded":
                            s3_upload_uploaded_count += 1
                        elif image_artifact.s3_upload_status == "skipped":
                            s3_upload_skipped_count += 1
                        
                        # Add the table image artifact to the list of images for stats and markdown generation
                        images.append(image_artifact)

                        # Add the markdown for the table image with a marker in the markdown text
                        markdown_parts.extend(
                            [
                                "> **Table (image)**: Table exists in image form.",
                                f"> {_table_image_uuid_marker(image_artifact.image_uuid)}",
                                f"> ![{table_image_name}]({table_image_name})",
                                "",
                            ]
                        )
                    except Exception as exc:
                        warnings.append(
                            f"Failed to export fallback table image #{table_index} on page {page_no}: {exc}"
                        )
                        markdown_parts.extend(
                            [
                                "> **Table (image)**: Table exists in image form.",
                                "> (Local crop failed: missing/invalid bbox or page_no.)",
                                "",
                            ]
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
                markdown_parts.append(serialized_text)

    if not markdown_parts:
        raise RuntimeError(
            "No markdown text serialized from Beam Docling endpoint response."
        )

    converted_chunks = 1
    print("Successfully extracted the PDF document using Beam endpoint")
    markdown_text = "\n\n".join(markdown_parts)
    markdown_path.write_text(markdown_text, encoding="utf-8") # Write the combined markdown text to the markdown file in the artifact directory

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
