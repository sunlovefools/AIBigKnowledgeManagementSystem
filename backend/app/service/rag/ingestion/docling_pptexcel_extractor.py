"""
Docling-based extraction for PowerPoint and Excel files (PPTX/XLSX).

Flow:
1. Convert Office bytes to a Docling document.
2. Build a normalized Docling layout from iterated items.
3. Reuse the shared Docling pipeline processor for markdown/images/VLM/chunking blocks.
4. Return Office parse result with the existing public contract.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from app.core.id_utils import generate_uuid_v6
from app.service.rag.ingestion.docling.models import (
    DoclingStructuredBlock,
    ExtractedImageArtifact,
)
from app.service.rag.ingestion.docling.storage import local_artifacts_store
from app.service.rag.ingestion.docling.utils import pdf_utils
from app.service.rag.ingestion.docling import pipeline as docling_pipeline

# MIME types for PowerPoint/Excel documents
SUPPORTED_PPTEXCEL_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
}


class DoclingPptExcelParseResult:
    """
    Result from parsing a PowerPoint or Excel file with Docling.

    Similar to DoclingParseResult but simplified for PPTX/XLSX documents.
    """

    def __init__(
        self,
        structured_blocks: list[DoclingStructuredBlock],
        markdown_content: str,
        warnings: list[str],
        file_id: str,
        file_name: str,
        images: list[ExtractedImageArtifact] | None = None,
    ):
        self.structured_blocks = structured_blocks
        self.markdown_content = markdown_content
        self.warnings = warnings
        self.file_id = file_id
        self.file_name = file_name
        self.images = images or []
        self.artifact_dir: Path | None = None
        self.artifact_run_id: str | None = None
        self.partial_failures: list[Any] = []


def _load_office_docling_runtime() -> dict[str, Any]:
    """
    Lazy-load Office Docling classes needed to build a normalized layout.
    """

    from docling.document_converter import DocumentConverter
    from docling_core.transforms.serializer.markdown import MarkdownDocSerializer
    from docling_core.types.doc import (
        ListItem,
        PictureItem,
        SectionHeaderItem,
        TableItem,
        TitleItem,
    )

    return {
        "DocumentConverter": DocumentConverter,
        "MarkdownDocSerializer": MarkdownDocSerializer,
        "ListItem": ListItem,
        "PictureItem": PictureItem,
        "SectionHeaderItem": SectionHeaderItem,
        "TableItem": TableItem,
        "TitleItem": TitleItem,
    }


def _normalize_status(status: Any) -> str:
    """Normalize conversion status to lowercase string."""

    value = getattr(status, "value", status)
    return str(value or "").strip().lower()


def _collect_result_errors(result: Any) -> list[str]:
    """Collect conversion errors from Docling convert result when available."""

    raw_errors = getattr(result, "errors", None) or []
    errors: list[str] = []
    for err in raw_errors:
        if err is None:
            continue
        try:
            message = str(err)
        except Exception:
            message = repr(err)
        if message:
            errors.append(message)
    return errors


def _extract_ref_value(raw_ref: Any) -> str | None:
    """
    Extract a normalized `#/...` reference string from Docling ref-like payloads.

    Handles strings, dicts with `$ref`, and ref objects exposing `cref`/`ref`.
    """

    if raw_ref is None:
        return None
    if isinstance(raw_ref, str):
        value = raw_ref.strip()
        return value or None
    if isinstance(raw_ref, dict):
        value = str(raw_ref.get("$ref") or "").strip()
        return value or None

    for attr in ("cref", "ref"):
        value = str(getattr(raw_ref, attr, "") or "").strip()
        if value:
            return value
    return None


def _normalize_sheet_name(raw_name: str) -> str:
    """
    Normalize Docling group sheet names from `sheet: <name>` to `<name>`.
    """

    name = (raw_name or "").strip()
    if not name:
        return ""
    if ":" in name:
        prefix, suffix = name.split(":", 1)
        if prefix.strip().lower() == "sheet":
            return suffix.strip()
    return name


def _build_excel_sheet_lookup(document: Any) -> tuple[dict[str, str], dict[str, str]]:
    """
    Build lookup maps for Excel sheets:
    - group_ref -> sheet_name
    - table_ref -> sheet_name (via group children refs)
    """

    group_ref_to_sheet_name: dict[str, str] = {}
    table_ref_to_sheet_name: dict[str, str] = {}

    groups = list(getattr(document, "groups", []) or [])
    for group in groups:
        group_ref = _extract_ref_value(getattr(group, "self_ref", None))
        sheet_name = _normalize_sheet_name(str(getattr(group, "name", "") or ""))
        if not group_ref or not sheet_name:
            continue

        group_ref_to_sheet_name[group_ref] = sheet_name
        for child in list(getattr(group, "children", []) or []):
            child_ref = _extract_ref_value(child)
            if child_ref:
                table_ref_to_sheet_name[child_ref] = sheet_name

    return group_ref_to_sheet_name, table_ref_to_sheet_name


def _convert_office_bytes_to_layout(
    *,
    file_bytes: bytes,
    file_name: str,
    warnings: list[str],
    partial_failures: list[Any],
) -> dict[str, Any]:
    """
    Convert Office bytes with Docling and build a normalized layout payload.

    The returned layout mirrors the contract used by the shared PDF pipeline core.
    """

    runtime = _load_office_docling_runtime()
    converter_cls = runtime["DocumentConverter"]

    extension = Path(file_name).suffix
    with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as temp_file:
        temp_file.write(file_bytes)
        temp_file_path = temp_file.name

    try:
        converter = converter_cls()
        result = converter.convert(temp_file_path)
    finally:
        try:
            os.unlink(temp_file_path)
        except Exception as exc:
            warnings.append(
                f"Could not delete temporary Office file {temp_file_path}: {exc}"
            )

    status_value = _normalize_status(getattr(result, "status", None))
    if status_value in {"failure", "skipped"}:
        errors = _collect_result_errors(result) or [
            f"Office conversion status={status_value}"
        ]
        partial_failures.append(
            {
                "page_range": "office-document",
                "errors": errors,
            }
        )
        raise RuntimeError("Office Docling conversion did not complete successfully")

    if status_value == "partial_success":
        partial_failures.append(
            {
                "page_range": "office-document",
                "errors": _collect_result_errors(result)
                or ["Office conversion returned partial_success with no errors list."],
            }
        )

    document = result.document
    serializer = runtime["MarkdownDocSerializer"](doc=document)
    group_ref_to_sheet_name: dict[str, str] = {}
    table_ref_to_sheet_name: dict[str, str] = {}
    if Path(file_name).suffix.lower() == ".xlsx":
        (
            group_ref_to_sheet_name,
            table_ref_to_sheet_name,
        ) = _build_excel_sheet_lookup(document)

    items: list[dict[str, Any]] = []
    for element, _level in document.iterate_items():
        table_data = getattr(element, "data", None)
        num_rows = getattr(table_data, "num_rows", None)
        num_cols = getattr(table_data, "num_cols", None)

        self_ref = _extract_ref_value(getattr(element, "self_ref", None))
        parent_ref = _extract_ref_value(getattr(element, "parent", None))
        sheet_name = ""
        if self_ref:
            sheet_name = table_ref_to_sheet_name.get(self_ref, "")
        if not sheet_name and parent_ref:
            sheet_name = group_ref_to_sheet_name.get(parent_ref, "")

        items.append(
            {
                "element": element,
                "serializer": serializer,
                "page_no": pdf_utils.extract_page_no(element),
                "document": document,
                "num_rows": num_rows if isinstance(num_rows, int) else None,
                "num_cols": num_cols if isinstance(num_cols, int) else None,
                "sheet_name": sheet_name,
                "sheet_ref": parent_ref,
            }
        )

    return {
        "items": items,
        "picture_item_cls": runtime["PictureItem"],
        "table_item_cls": runtime["TableItem"],
        "list_item_cls": runtime["ListItem"],
        "section_header_item_cls": runtime["SectionHeaderItem"],
        "title_item_cls": runtime["TitleItem"],
        "converted_chunks": 1,
    }


def _serialize_partial_failures(partial_failures: list[Any]) -> list[Any]:
    """Serialize mixed partial-failure entries into JSON-compatible payloads."""

    serialized: list[Any] = []
    for failure in partial_failures:
        if hasattr(failure, "model_dump"):
            serialized.append(failure.model_dump())
        elif isinstance(failure, (dict, str, int, float, bool)) or failure is None:
            serialized.append(failure)
        else:
            serialized.append(str(failure))
    return serialized


def is_pptexcel_document(content_type: str) -> bool:
    """
    Check if a content type is a supported PowerPoint/Excel document format.
    """

    return content_type in SUPPORTED_PPTEXCEL_MIME_TYPES


def parse_pptexcel_with_docling(
    file_bytes: bytes,
    file_name: str,
    content_type: str,
    file_id: str | None = None,
) -> DoclingPptExcelParseResult:
    """
    Parse PowerPoint/Excel file with Docling and emit chunker-compatible structured blocks.
    """

    if not file_bytes:
        raise ValueError("empty file payload")

    if not is_pptexcel_document(content_type):
        raise ValueError(
            f"Unsupported content type for Docling PPTX/XLSX extraction: {content_type}. "
            f"Supported types: {', '.join(SUPPORTED_PPTEXCEL_MIME_TYPES)}"
        )

    file_type = "PowerPoint" if "presentation" in content_type else "Excel"
    print(
        f"[docling-pptexcel] Processing {file_type} file: {file_name} ({len(file_bytes)} bytes)"
    )

    resolved_file_id = (file_id or generate_uuid_v6()).strip()
    warnings: list[str] = []
    partial_failures: list[Any] = []

    try:
        layout = _convert_office_bytes_to_layout(
            file_bytes=file_bytes,
            file_name=file_name,
            warnings=warnings,
            partial_failures=partial_failures,
        )

        run_id, artifact_dir, markdown_path = local_artifacts_store.prepare_docling_artifact_dir(
            file_name=file_name,
            artifact_root=None,
        )
        artifacts_enabled = artifact_dir is not None and markdown_path is not None

        outputs = docling_pipeline._process_docling_layout(
            layout=layout,
            file_name=file_name,
            resolved_file_id=resolved_file_id,
            artifact_dir=artifact_dir,
            markdown_path=markdown_path,
            image_export_mode="local",
            warnings=warnings,
            partial_failures=partial_failures,
            empty_markdown_error=(
                f"No markdown text serialized from Docling {file_type} response."
            ),
        )

        parse_result = DoclingPptExcelParseResult(
            structured_blocks=outputs["structured_blocks"],
            markdown_content=outputs["markdown_text"],
            warnings=outputs["warnings"],
            file_id=resolved_file_id,
            file_name=file_name,
            images=outputs["images"],
        )
        parse_result.artifact_dir = artifact_dir
        parse_result.artifact_run_id = run_id or ""
        parse_result.partial_failures = outputs["partial_failures"]

        if artifacts_enabled and artifact_dir is not None and markdown_path is not None:
            local_artifacts_store.write_manifest(
                artifact_dir,
                {
                    "source_file_name": file_name,
                    "artifact_run_id": run_id or "",
                    "artifact_dir": str(artifact_dir),
                    "markdown_path": str(markdown_path),
                    "markdown_text": outputs["markdown_text"],
                    "images": [image.model_dump() for image in outputs["images"]],
                    "warnings": outputs["warnings"],
                    "partial_failures": _serialize_partial_failures(
                        outputs["partial_failures"]
                    ),
                    "stats": outputs["stats"].model_dump(),
                    "structured_blocks": [
                        block.model_dump() for block in outputs["structured_blocks"]
                    ],
                },
            )

        print(
            "[docling-office] file=%s run_id=%s chunks=%s pictures=%s "
            "table_fallbacks=%s partial_failures=%s s3_uploaded=%s s3_failed=%s s3_skipped=%s"
            % (
                file_name,
                run_id,
                outputs["stats"].converted_chunks,
                outputs["stats"].pictures_extracted,
                outputs["stats"].table_fallback_images_extracted,
                outputs["stats"].partial_failure_chunks,
                outputs["s3_upload_uploaded_count"],
                outputs["s3_upload_failed_count"],
                outputs["s3_upload_skipped_count"],
            )
        )

        return parse_result
    except Exception as exc:
        error_msg = f"Docling {file_type} processing failed for {file_name}: {exc}"
        print(f"[docling-pptexcel] ERROR: {error_msg}")
        raise Exception(error_msg) from exc


def get_pptexcel_ingestion_strategy() -> str:
    """
    Determine the PowerPoint/Excel document ingestion strategy.
    """

    return "docling"


__all__ = [
    "SUPPORTED_PPTEXCEL_MIME_TYPES",
    "DoclingPptExcelParseResult",
    "is_pptexcel_document",
    "parse_pptexcel_with_docling",
    "get_pptexcel_ingestion_strategy",
]
