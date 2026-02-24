import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from uuid6 import uuid6
from app.service.storage.s3_image_store import (
    build_s3_image_key,
    _load_s3_config,
    upload_file_to_s3,
)


DEFAULT_DOCLING_PAGE_CHUNK_SIZE = 6 # Default number of pages to process in each chunk when parsing PDFs with Docling.


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
    page_chunk_size: int
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


def _load_docling_runtime() -> dict[str, Any]:
    """
    Lazy import Docling classes and return them in a dictionary for use in the conversion process.

    This approach avoids importing Docling at the module level, which can be beneficial for performance and resource usage, 
    especially if Docling is only needed for specific PDF parsing strategies.
    """
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import ConversionStatus, InputFormat
    from docling.datamodel.pipeline_options import (
        AcceleratorOptions,
        ThreadedPdfPipelineOptions,
    )
    from docling_core.transforms.serializer.markdown import MarkdownDocSerializer
    from docling_core.types.doc import PictureItem, TableItem

    return {
        "DocumentConverter": DocumentConverter,
        "PdfFormatOption": PdfFormatOption,
        "InputFormat": InputFormat,
        "ConversionStatus": ConversionStatus,
        "ThreadedPdfPipelineOptions": ThreadedPdfPipelineOptions,
        "AcceleratorOptions": AcceleratorOptions,
        "MarkdownDocSerializer": MarkdownDocSerializer,
        "PictureItem": PictureItem,
        "TableItem": TableItem,
    }


def _build_converter_pipeline(runtime: dict[str, Any]) -> Any:
    """Build and configure a Docling DocumentConverter with options optimized for preview extraction."""
    pipeline_options = runtime["ThreadedPdfPipelineOptions"]()
    pipeline_options.accelerator_options = runtime["AcceleratorOptions"](
        device="cpu",
        num_threads=4, # Adjust based on expected workload and environment capabilities
        cuda_use_flash_attention2=False,
    )

    pipeline_options.do_ocr = False  # Disable OCR for better performance
    pipeline_options.do_table_structure = True # Enable table structure extraction to get rows/columns when possible
    pipeline_options.generate_table_images = True # Enable generation of table images when structure extraction fails
    pipeline_options.generate_picture_images = True # Enable generation of picture images for better preview quality
    pipeline_options.images_scale = 2.0
    pipeline_options.generate_page_images = False
    pipeline_options.do_chart_extraction = False
    pipeline_options.do_formula_enrichment = False
    pipeline_options.layout_batch_size = 4 # Process multiple pages in parallel for layout analysis
    pipeline_options.table_batch_size = 2 # Process multiple tables in parallel for structure extraction

    # Build the pipeline options into the format options for the PDF converter
    return runtime["DocumentConverter"](
        format_options={
            runtime["InputFormat"].PDF: runtime["PdfFormatOption"](
                pipeline_options=pipeline_options
            )
        }
    )


def _build_converter(runtime: dict[str, Any]) -> Any:
    """
    Backward-compatible alias for older tests/call sites.
    """
    return _build_converter_pipeline(runtime)


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


def parse_pdf_with_docling_preview(
    pdf_bytes: bytes,
    file_name: str,
    artifact_root: Path | None = None,
    page_chunk_size: int = DEFAULT_DOCLING_PAGE_CHUNK_SIZE,
) -> DoclingParseResult:
    """
    Parse a PDF with Docling and persist preview artifacts (markdown + extracted images).
    """
    if not pdf_bytes:
        raise ValueError("empty pdf payload")

    runtime = _load_docling_runtime() # Load the runtime classes
    converter = _build_converter(runtime) # Build the Docling pipeline converter with optimized options for preview extraction

    preview_root = Path(artifact_root) if artifact_root else _default_preview_root()
    preview_root.mkdir(parents=True, exist_ok=True)

    # Each run will have its own unique artifact directory
    run_id = str(uuid6())
    artifact_dir = preview_root / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # The markdown file that will contain the extracted text content from the PDF, with references to extracted images
    safe_stem = _safe_stem(file_name)
    markdown_path = artifact_dir / "document.md"

    markdown_chunks: list[str] = []
    images: list[ExtractedImageArtifact] = [] # A list to hold metadata about extracted images
    warnings: list[str] = []
    partial_failures: list[DoclingChunkFailure] = []
    s3_upload_failed_count = 0
    s3_upload_uploaded_count = 0
    s3_upload_skipped_count = 0

    converted_chunks = 0
    current_start = 1 # For tracking the starting page number of each chunk
    picture_counter = 0 # For tracking the number of pictures extracted, used in naming the picture artifacts
    table_counter = 0 # For tracking the number of tables processed, used in naming extracted table image artifacts
    table_image_count = 0 # For tracking the number of extracted table images used in stats and logging

    temp_pdf_path: Path | None = None
    try:
        # Writing the PDF bytes to a temporary file because Docling's converter expects a file path.
        with tempfile.NamedTemporaryFile(
            suffix=".pdf", delete=False
        ) as temp_pdf_file:
            temp_pdf_file.write(pdf_bytes)
            temp_pdf_path = Path(temp_pdf_file.name)

        conversion_status = runtime["ConversionStatus"]
        serializer_cls = runtime["MarkdownDocSerializer"]
        picture_item_cls = runtime["PictureItem"]
        table_item_cls = runtime["TableItem"]

        while True:
            # Loop through the PDF in chunks of pages to avoid processing the entire document at once
            current_end = current_start + page_chunk_size - 1
            page_range_label = f"{current_start}-{current_end}"
            print(f"[docling-preview] Converting pages {page_range_label} ...")
            result = converter.convert(
                str(temp_pdf_path),
                raises_on_error=False,
                page_range=(current_start, current_end),
            )

            if result.status in {conversion_status.FAILURE, conversion_status.SKIPPED}:
                warnings.append(f"Chunk {page_range_label} conversion failed with status {result.status}.")
                break

            if getattr(result, "document", None) is None:
                warnings.append(f"Chunk {page_range_label} produced no document.")
                current_start += page_chunk_size
                continue

            converted_chunks += 1
            serializer = serializer_cls(doc=result.document) # Serializer for converting Docling document items into markdown text
            chunk_markdown_parts: list[str] = []

            for element, _ in result.document.iterate_items():
                page_no = _extract_page_no(element)

                # If the element is a PictureItem (pictire_item_cls), extract the image and save it as a PNG file in the artifact directory, 
                # and add a reference to it in the markdown.
                if isinstance(element, picture_item_cls):
                    picture_counter += 1
                    picture_name = f"{safe_stem}-picture-{picture_counter}.png" # 
                    picture_path = artifact_dir / picture_name
                    image_uuid = str(uuid6())
                    try:
                        with picture_path.open("wb") as fp:
                            element.get_image(result.document).save(fp, "PNG")
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
                    except Exception as exc:
                        warnings.append(
                            f"Failed to export picture #{picture_counter} on page {page_no}: {exc}"
                        )

                # If the element is a TableItem (table_item_cls), check if it has valid rows and columns. 
                # If not, extract a fallback image of the table and save it as a PNG file in the artifact directory, 
                # and add a reference to it in the markdown with an explanation. If it does have valid rows and columns, 
                # serialize it as markdown text as usual.
                if isinstance(element, table_item_cls):
                    table_counter += 1
                    table_index = table_counter
                    table_data = getattr(element, "data", None)
                    num_rows = getattr(table_data, "num_rows", None)
                    num_cols = getattr(table_data, "num_cols", None)
                    if num_rows == 0 or num_cols == 0:
                        table_image_count += 1

                        # Using a UUID in the table image name to ensure uniqueness.
                        # It is also a move for future when we want to upload these images to a persistent storage and want to avoid name collisions.
                        table_image_name = (
                            f"{safe_stem}-table-{table_index}-{uuid6()}.png"
                        )
                        table_image_path = artifact_dir / table_image_name
                        image_uuid = str(uuid6())
                        try:
                            with table_image_path.open("wb") as fp:
                                element.get_image(result.document).save(fp, "PNG")
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
                            # Add a reference to the fallback table image in the markdown.
                            chunk_markdown_parts.extend(
                                [
                                    "> **Table (image)**: Structure extraction failed (rows/cols = 0).",
                                    f"> ![{table_image_name}]({table_image_name})",
                                    "",
                                ]
                            )
                        except Exception as exc:
                            warnings.append(
                                f"Failed to export fallback table image #{table_index} on page {page_no}: {exc}"
                            )
                        continue

                # For each element, it will be serialised into markdown and added to the chunk's markdown content.
                try:
                    serialized_text = serializer.serialize(item=element).text.strip()
                except Exception as exc:
                    warnings.append(
                        f"Failed to serialize element on page {page_no} in chunk {page_range_label}: {exc}"
                    )
                    continue

                if serialized_text:
                    chunk_markdown_parts.append(serialized_text)

            chunk_markdown = "\n\n".join(chunk_markdown_parts).strip()
            if chunk_markdown:
                markdown_chunks.append(chunk_markdown)

            if (
                result.status == conversion_status.PARTIAL_SUCCESS
                and getattr(result, "errors", None)
            ):
                partial_failures.append(
                    DoclingChunkFailure(
                        page_range=page_range_label,
                        errors=[str(err) for err in result.errors],
                    )
                )

            current_start += page_chunk_size

    finally:
        if temp_pdf_path and temp_pdf_path.exists():
            try:
                temp_pdf_path.unlink() # Clean up the temporary PDF file after processing
            except OSError:
                pass

    if converted_chunks == 0 or not markdown_chunks:
        raise RuntimeError(
            "No pages converted successfully. Try a smaller page chunk size for constrained environments."
        )

    markdown_text = "\n\n".join(markdown_chunks)
    markdown_path.write_text(markdown_text, encoding="utf-8") # Write the combined markdown text to the markdown file in the artifact directory

    stats = DoclingParseStats(
        page_chunk_size=page_chunk_size,
        converted_chunks=converted_chunks,
        partial_failure_chunks=len(partial_failures),
        pictures_extracted=sum(1 for item in images if item.kind == "picture"),
        table_fallback_images_extracted=table_image_count,
    )

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
