"""
Image extraction/export helpers for Docling layout processing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
from uuid6 import uuid6

from app.service.rag.ingestion.docling.clients import local_client
from app.service.rag.ingestion.docling.models import ExtractedImageArtifact
from app.service.rag.ingestion.docling.storage import local_artifacts_store, s3_upload
from app.service.rag.ingestion.docling.utils import pdf_utils


@dataclass
class S3UploadCounters:
    """Aggregate counters for picture/table image uploads."""

    failed_count: int = 0
    uploaded_count: int = 0
    skipped_count: int = 0


def extract_png_bytes_for_item(
    *,
    image_export_mode: str,
    item: dict[str, Any],
    element: Any,
    pdf_doc: fitz.Document | None,
) -> bytes | None:
    """Extract PNG bytes from a Docling item using the selected image export mode."""

    if image_export_mode == "beam":
        endpoint_item = item.get("endpoint_item", {})
        if pdf_doc is None:
            return None
        return pdf_utils.crop_image_bytes_from_endpoint_item(endpoint_item, pdf_doc)

    return local_client._extract_png_bytes_from_local_element(
        element,
        item.get("document"),
    )


def upload_extracted_image_artifact(
    *,
    image_artifact: ExtractedImageArtifact,
    source_file_name: str,
    file_id: str,
    warnings: list[str],
    counters: S3UploadCounters,
) -> ExtractedImageArtifact:
    """
    Upload one extracted image artifact to S3 and update upload counters/warnings.
    """

    uploaded_artifact = s3_upload.upload_image_artifact_to_s3(
        image_artifact,
        source_file_name=source_file_name,
        file_id=file_id,
    )

    if uploaded_artifact.s3_upload_status == "failed":
        counters.failed_count += 1
        warnings.append(
            "Failed to upload %s image_uuid=%s to S3: %s"
            % (
                uploaded_artifact.kind.replace("_", " "),
                uploaded_artifact.image_uuid,
                uploaded_artifact.s3_error,
            )
        )
    elif uploaded_artifact.s3_upload_status == "uploaded":
        counters.uploaded_count += 1
    elif uploaded_artifact.s3_upload_status == "skipped":
        counters.skipped_count += 1

    return uploaded_artifact


def prepare_picture_artifact_paths(
    *,
    artifact_dir: Path,
) -> tuple[str, str, Path]:
    """Generate UUID/name/path for one extracted picture artifact."""

    image_uuid = str(uuid6())
    picture_name = local_artifacts_store.image_file_name_from_uuid(image_uuid)
    picture_path = local_artifacts_store.image_file_path_from_uuid(
        artifact_dir,
        image_uuid,
    )
    return image_uuid, picture_name, picture_path

