"""
Local Docling runtime helpers and chunked layout preparation.
"""

from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Any

from app.service.rag.ingestion.docling.config import (
    DEFAULT_DOCLING_PAGE_CHUNK_SIZE,
    LOCAL_DOCLING_CHUNK_SIZE,
    LOCAL_DOCLING_LAYOUT_BATCH_SIZE,
    LOCAL_DOCLING_TABLE_BATCH_SIZE,
)
from app.service.rag.ingestion.docling.models import DoclingChunkFailure
from app.service.rag.ingestion.docling.storage.local_artifacts_store import (
    stringify_endpoint_error,
)
from app.service.rag.ingestion.docling.utils.pdf_utils import extract_page_no

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
    Build the local Docling converter with low-memory CUDA config.
    """

    runtime = _load_local_docling_runtime()
    pipeline_options = runtime["ThreadedPdfPipelineOptions"]()
    pipeline_options.accelerator_options = runtime["AcceleratorOptions"](
        device="cuda",
        num_threads=8,
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
    print(f"Accelerator device: {pipeline_options.accelerator_options.device}")
    return converter


def _get_or_create_local_converter() -> Any:
    """
    Return cached local converter, creating it on first use.
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
    Normalize conversion status to lowercase string.
    """

    value = getattr(status, "value", status)
    return str(value or "").strip().lower()


def _collect_result_errors(result: Any, *, fallback: str) -> list[str]:
    """
    Collect and stringify errors from a local Docling conversion result.
    """

    raw_errors = getattr(result, "errors", None) or []
    errors = [stringify_endpoint_error(err) for err in raw_errors if err is not None]
    return errors or [fallback]


def _extract_png_bytes_from_local_element(element: Any, document: Any) -> bytes | None:
    """
    Extract PNG bytes for a local picture/table item via `get_image(document)`.
    """

    try:
        image = element.get_image(document)
    except Exception:
        return None
    if image is None:
        return None

    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def build_local_layout(
    *,
    pdf_bytes: bytes,
    file_name: str,
    page_chunk_size: int,
    warnings: list[str],
    partial_failures: list[DoclingChunkFailure],
) -> dict[str, Any]:
    """
    Build normalized layout items by running local Docling conversion in page chunks.
    """

    effective_page_chunk_size = (
        page_chunk_size
        if isinstance(page_chunk_size, int) and page_chunk_size > 0
        else LOCAL_DOCLING_CHUNK_SIZE
    )

    runtime = _load_local_docling_runtime()
    converter = _get_or_create_local_converter()

    picture_item_cls = runtime["PictureItem"]
    table_item_cls = runtime["TableItem"]
    list_item_cls = runtime.get("ListItem")
    section_header_item_cls = runtime.get("SectionHeaderItem")
    title_item_cls = runtime.get("TitleItem")
    markdown_serializer_cls = runtime["MarkdownDocSerializer"]
    document_stream_cls = runtime["DocumentStream"]

    discovered_total_pages: int | None = None
    current_start = 1
    converted_chunks = 0
    items: list[dict[str, Any]] = []

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
            result = converter.convert(
                doc_stream,
                raises_on_error=False,
                page_range=(current_start, current_end),
            )
        except Exception as exc:
            partial_failures.append(
                DoclingChunkFailure(page_range=page_range_label, errors=[str(exc)])
            )
            warnings.append(
                f"Local Docling chunk {page_range_label} conversion exception: {exc}"
            )
            if discovered_total_pages is None:
                break
            current_start += effective_page_chunk_size
            if current_start > discovered_total_pages:
                break
            continue

        result_input = getattr(result, "input", None)
        result_page_count = getattr(result_input, "page_count", None)
        if (
            discovered_total_pages is None
            and isinstance(result_page_count, int)
            and result_page_count > 0
        ):
            discovered_total_pages = result_page_count

        status_value = _normalize_status(getattr(result, "status", None))
        if status_value in {"failure", "skipped"}:
            errors = _collect_result_errors(
                result,
                fallback=f"Chunk conversion status={status_value}",
            )
            partial_failures.append(
                DoclingChunkFailure(page_range=page_range_label, errors=errors)
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
                DoclingChunkFailure(
                    page_range=page_range_label,
                    errors=_collect_result_errors(
                        result,
                        fallback="Chunk returned partial_success with no errors list.",
                    ),
                )
            )

        serializer = markdown_serializer_cls(doc=result.document)
        for element, _level in result.document.iterate_items():
            page_no = extract_page_no(element)
            table_data = getattr(element, "data", None)
            num_rows = getattr(table_data, "num_rows", None)
            num_cols = getattr(table_data, "num_cols", None)
            items.append(
                {
                    "element": element,
                    "serializer": serializer,
                    "page_no": page_no,
                    "document": result.document,
                    "num_rows": num_rows if isinstance(num_rows, int) else None,
                    "num_cols": num_cols if isinstance(num_cols, int) else None,
                }
            )

        if discovered_total_pages is not None and current_end >= discovered_total_pages:
            break

        current_start += effective_page_chunk_size

    return {
        "items": items,
        "picture_item_cls": picture_item_cls,
        "table_item_cls": table_item_cls,
        "list_item_cls": list_item_cls,
        "section_header_item_cls": section_header_item_cls,
        "title_item_cls": title_item_cls,
        "converted_chunks": converted_chunks,
    }


def parse_pdf_with_docling_preview_local(
    pdf_bytes: bytes,
    file_name: str,
    artifact_root: Path | None = None,
    page_chunk_size: int = DEFAULT_DOCLING_PAGE_CHUNK_SIZE,
    file_id: str | None = None,
) -> Any:
    """
    Local-backend convenience entrypoint routed through the unified pipeline.
    """

    from app.service.rag.ingestion.docling.pipeline import parse_pdf_with_docling_preview

    return parse_pdf_with_docling_preview(
        pdf_bytes=pdf_bytes,
        file_name=file_name,
        artifact_root=artifact_root,
        page_chunk_size=page_chunk_size,
        file_id=file_id,
        backend="local",
    )
