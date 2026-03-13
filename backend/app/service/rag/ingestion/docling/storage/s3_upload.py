"""
S3 upload helpers for Docling image and table-data artifacts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.service.rag.ingestion.docling.models import ExtractedImageArtifact
from app.service.storage.s3_image_store import (
    _load_s3_config,
    build_s3_docling_artifact_key,
    build_s3_image_key,
    upload_bytes_to_s3,
    upload_file_to_s3,
)


def _require_s3_upload_enabled() -> bool:
    """
    Validate AWS_S3_UPLOAD_ENABLED and return whether uploads should proceed.
    """

    raw_upload_enabled = os.getenv("AWS_S3_UPLOAD_ENABLED")
    if raw_upload_enabled is None:
        raise RuntimeError(
            "AWS_S3_UPLOAD_ENABLED is required and must be set to 'true' or 'false'."
        )

    upload_enabled = raw_upload_enabled.strip().lower()
    if upload_enabled not in {"true", "false"}:
        raise RuntimeError(
            "AWS_S3_UPLOAD_ENABLED must be set to 'true' or 'false', "
            f"got: {raw_upload_enabled!r}."
        )

    return upload_enabled == "true"


def upload_image_artifact_to_s3(
    image_artifact: ExtractedImageArtifact,
    *,
    source_file_name: str,
    file_id: str | None = None,
) -> ExtractedImageArtifact:
    """
    Best-effort S3 upload for a locally saved image artifact.

    This function mutates and returns the image artifact with S3 metadata/status.
    """

    if not _require_s3_upload_enabled():
        image_artifact.s3_upload_status = "skipped"
        image_artifact.s3_error = "S3 upload disabled (AWS_S3_UPLOAD_ENABLED=false)"
        print(
            f"S3 upload skipped for image_uuid={image_artifact.image_uuid} "
            "because AWS_S3_UPLOAD_ENABLED=false."
        )
        return image_artifact

    try:
        s3_config = _load_s3_config()
        if s3_config is None:
            image_artifact.s3_upload_status = "skipped"
            image_artifact.s3_error = "S3 upload disabled (missing config)"
            return image_artifact

        extension = Path(image_artifact.file_name).suffix or ".png"
        artifact_type = "table_image" if image_artifact.kind == "table_image" else "image"
        if file_id:
            s3_key = build_s3_docling_artifact_key(
                file_id=file_id,
                artifact_uuid=image_artifact.image_uuid,
                artifact_type=artifact_type,
                extension=extension,
                prefix=s3_config.prefix,
            )
        else:
            s3_key = build_s3_image_key(
                image_uuid=image_artifact.image_uuid,
                extension=extension,
                prefix=s3_config.prefix,
                source_file_name=source_file_name,
            )

        upload_result = upload_file_to_s3(
            local_path=image_artifact.file_path,
            key=s3_key,
            content_type="image/png",
            metadata={
                "image_uuid": image_artifact.image_uuid,
                "kind": image_artifact.kind,
                "source_file_name": source_file_name,
                "file_id": file_id or "",
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


def upload_table_data_json_to_s3(
    *,
    json_bytes: bytes,
    file_id: str,
    table_image_uuid: str,
    source_file_name: str,
    page_no: int | None,
) -> dict[str, Any] | None:
    """
    Best-effort upload for processed table-data JSON artifacts.
    """

    if not _require_s3_upload_enabled():
        print(
            f"S3 upload skipped for table_image_uuid={table_image_uuid} "
            "because AWS_S3_UPLOAD_ENABLED=false."
        )
        return None

    s3_config = _load_s3_config()
    if s3_config is None:
        return None

    s3_key = build_s3_docling_artifact_key(
        file_id=file_id,
        artifact_uuid=table_image_uuid,
        artifact_type="table_data",
        extension=".json",
        prefix=s3_config.prefix,
    )
    result = upload_bytes_to_s3(
        data=json_bytes,
        key=s3_key,
        content_type="application/json",
        metadata={
            "kind": "table_data",
            "table_image_uuid": table_image_uuid,
            "source_file_name": source_file_name,
            "file_id": file_id,
            "page_no": "" if page_no is None else str(page_no),
        },
        config=s3_config,
    )
    return result.model_dump()


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

