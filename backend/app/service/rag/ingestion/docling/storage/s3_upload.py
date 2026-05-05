"""Legacy S3 helper interface retained as local-only no-op wrappers."""

from __future__ import annotations

from typing import Any

from app.service.rag.ingestion.docling.models import ExtractedImageArtifact


def _require_s3_upload_enabled() -> bool:
    """
    S3 ingestion uploads are permanently disabled.
    """
    return False


def upload_image_artifact_to_s3(
    image_artifact: ExtractedImageArtifact,
    *,
    source_file_name: str,
    file_id: str | None = None,
) -> ExtractedImageArtifact:
    """
    Legacy API retained for compatibility; artifacts are now local-only.
    """
    _ = source_file_name
    _ = file_id
    image_artifact.s3_upload_status = "skipped"
    image_artifact.s3_error = "S3 upload removed; artifact is stored locally only."
    return image_artifact


def upload_table_data_json_to_s3(
    *,
    json_bytes: bytes,
    file_id: str,
    table_image_uuid: str,
    source_file_name: str,
    page_no: int | None,
) -> dict[str, Any] | None:
    """
    Legacy API retained for compatibility; table-data payload stays local-only.
    """
    _ = json_bytes
    _ = file_id
    _ = table_image_uuid
    _ = source_file_name
    _ = page_no
    return None


def build_toon_wrapped_table_payload(
    *,
    extracted_table_json: Any,
    file_id: str,
    page_no: int | None,
) -> dict[str, Any]:
    """
    Convert extracted table JSON into TOON and wrap in the required schema.
    """

    try:
        from py_toon_format import encode
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing required dependency 'py-toon-format'. "
            "Install it to enable table-data TOON conversion."
        ) from exc

    toon_payload = encode(extracted_table_json)
    return {
        "data": {
            "toon": toon_payload,
        },
        "metadata": {
            "source": {
                "file_id": file_id,
                "page_number": page_no if isinstance(page_no, int) and page_no > 0 else 0,
            }
        },
    }

