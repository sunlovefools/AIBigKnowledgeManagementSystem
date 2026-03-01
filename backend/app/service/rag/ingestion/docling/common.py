# Module purpose:
# Shared Docling ingestion constants, data models, and utility helpers used by both
# the local and Beam-based Docling extraction backends.

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from uuid6 import uuid6

from app.service.storage.s3_image_store import (
    _load_s3_config,
    build_s3_image_key,
    upload_file_to_s3,
)

# Configuration constanst
DEFAULT_DOCLING_PAGE_CHUNK_SIZE = 6
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


class DoclingStructuredBlock(BaseModel):
    """
    Normalized Docling markdown block used by structure-aware chunking.
    """

    block_index: int
    block_type: Literal["header", "text", "list", "picture", "table", "other"]
    content: str
    page_no: int | None = None
    is_table_image: bool = False
    table_image_uuid: str | None = None


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
    structured_blocks: list[DoclingStructuredBlock] = Field(default_factory=list)


def _backend_root() -> Path:
    """
    Get the root directory of the backend project.
    """
    current_path = Path(__file__).resolve()
    for parent in current_path.parents:
        if parent.name == "backend":
            return parent
    raise RuntimeError(f"Could not locate backend root from path: {current_path}")


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

    _ = file_name  # kept in signature for stable call sites / future naming changes
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
    """

    stem = Path(file_name or "document.pdf").stem or "document"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem or "document"


def _artifact_images_dir(artifact_dir: Path) -> Path:
    """
    Return the per-run image artifact directory and ensure it exists.
    """

    images_dir = artifact_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    return images_dir


def _image_file_name_from_uuid(
    image_uuid: str,
    *,
    extension: str = ".png",
) -> str:
    """
    Build the local image filename from image UUID only.
    """

    normalized_ext = extension if extension.startswith(".") else f".{extension}"
    return f"{image_uuid}{normalized_ext}"


def _image_file_path_from_uuid(
    artifact_dir: Path,
    image_uuid: str,
    *,
    extension: str = ".png",
) -> Path:
    """
    Return the local image path under `<artifact_dir>/images/<image_uuid>.png`.
    """

    return _artifact_images_dir(artifact_dir) / _image_file_name_from_uuid(
        image_uuid,
        extension=extension,
    )


def _image_markdown_rel_path_from_uuid(
    image_uuid: str,
    *,
    extension: str = ".png",
) -> str:
    """
    Return markdown-friendly relative path `images/<image_uuid>.png`.
    """

    return (
        Path("images") / _image_file_name_from_uuid(image_uuid, extension=extension)
    ).as_posix()


def _extract_page_no(doc_item: Any) -> int | None:
    """
    Extract the page number from a Docling document item, if available.
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


def _stringify_endpoint_error(error: Any) -> str:
    """
    Convert error payload entries into readable strings for warnings/manifests.
    """

    if isinstance(error, str):
        return error
    try:
        return json.dumps(error, ensure_ascii=False)
    except Exception:
        return str(error)


def _upload_image_artifact_to_s3(
    image_artifact: ExtractedImageArtifact,
    *,
    source_file_name: str,
) -> ExtractedImageArtifact:
    """
    Best-effort S3 upload for a locally saved image artifact.

    This function mutates and returns the image artifact with S3 metadata/status.
    """

    raw_upload_enabled = os.getenv("AWS_S3_UPLOAD_ENABLED")
    if raw_upload_enabled is None:
        raise RuntimeError(
            "AWS_S3_UPLOAD_ENABLED is required and must be set to 'true' or 'false'."
        )

    upload_enabled = raw_upload_enabled.strip().lower()
    if upload_enabled not in {"true", "false"}:
        raise RuntimeError(
            "AWS_S3_UPLOAD_ENABLED must be set to 'true' or 'false', got: {raw_upload_enabled!r}."
        )

    if upload_enabled == "false":
        image_artifact.s3_upload_status = "skipped"
        image_artifact.s3_error = f"S3 upload disabled (AWS_S3_UPLOAD_ENABLED=false)"
        print(
            f"S3 upload skipped for image_uuid={image_artifact.image_uuid} "
            f"because AWS_S3_UPLOAD_ENABLED=false."
        )
        return image_artifact

    try:
        s3_config = _load_s3_config()
        if s3_config is None:
            image_artifact.s3_upload_status = "skipped"
            image_artifact.s3_error = "S3 upload disabled (missing config)"
            return image_artifact

        s3_key = build_s3_image_key(
            image_uuid=image_artifact.image_uuid,
            extension=Path(image_artifact.file_name).suffix or ".png",
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
    """

    manifest_path = artifact_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


__all__ = [
    "DEFAULT_DOCLING_PAGE_CHUNK_SIZE",
    "DOCLING_IMAGE_PLACEHOLDER",
    "DOCLING_IMAGE_CROP_FAILED_MARKER",
    "ExtractedImageArtifact",
    "DoclingChunkFailure",
    "DoclingParseStats",
    "DoclingStructuredBlock",
    "DoclingParseResult",
    "_backend_root",
    "_default_preview_root",
    "_prepare_docling_preview_artifact_dir",
    "_safe_stem",
    "_artifact_images_dir",
    "_image_file_name_from_uuid",
    "_image_file_path_from_uuid",
    "_image_markdown_rel_path_from_uuid",
    "_extract_page_no",
    "_picture_uuid_marker",
    "_table_image_uuid_marker",
    "_inject_marker_for_picture",
    "_stringify_endpoint_error",
    "_upload_image_artifact_to_s3",
    "_write_manifest",
]
