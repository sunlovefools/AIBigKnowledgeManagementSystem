"""
Local artifact storage helpers for Docling runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from uuid6 import uuid6

from app.service.rag.ingestion.docling.config import is_docling_artifacts_enabled


def backend_root() -> Path:
    """
    Get the root directory of the backend project.
    """

    current_path = Path(__file__).resolve()
    for parent in current_path.parents:
        if parent.name == "backend":
            return parent
    raise RuntimeError(f"Could not locate backend root from path: {current_path}")


def default_artifact_root() -> Path:
    """
    Get the default directory for storing Docling artifacts.
    """

    return backend_root() / "_local_uploads" / "docling_artifacts"


def prepare_docling_artifact_dir(
    *,
    file_name: str,
    artifact_root: Path | None = None,
) -> tuple[str, Path | None, Path | None]:
    """
    Prepare the per-run artifact directory and markdown output path.

    If DOCLING_ARTIFACTS_ENABLED is false, artifact persistence is disabled and
    empty path state is returned (run_id="", artifact_dir=None, markdown_path=None).
    """

    if not is_docling_artifacts_enabled():
        return "", None, None

    _ = file_name  # kept for stable call sites / future naming changes
    artifact_base = Path(artifact_root) if artifact_root else default_artifact_root()
    artifact_base.mkdir(parents=True, exist_ok=True)

    run_id = str(uuid6())
    artifact_dir = artifact_base / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    markdown_path = artifact_dir / "document.md"
    return run_id, artifact_dir, markdown_path


def safe_stem(file_name: str) -> str:
    """
    Generate a safe stem for the given file name.
    """

    stem = Path(file_name or "document.pdf").stem or "document"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem or "document"


def artifact_images_dir(artifact_dir: Path) -> Path:
    """
    Return the per-run image artifact directory and ensure it exists.
    """

    images_dir = artifact_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    return images_dir


def image_file_name_from_uuid(
    image_uuid: str,
    *,
    extension: str = ".png",
) -> str:
    """
    Build the local image filename from image UUID only.
    """

    normalized_ext = extension if extension.startswith(".") else f".{extension}"
    return f"{image_uuid}{normalized_ext}"


def image_file_path_from_uuid(
    artifact_dir: Path,
    image_uuid: str,
    *,
    extension: str = ".png",
) -> Path:
    """
    Return the local image path under `<artifact_dir>/images/<image_uuid>.png`.
    """

    return artifact_images_dir(artifact_dir) / image_file_name_from_uuid(
        image_uuid,
        extension=extension,
    )


def image_markdown_rel_path_from_uuid(
    image_uuid: str,
    *,
    extension: str = ".png",
) -> str:
    """
    Return markdown-friendly relative path `images/<image_uuid>.png`.
    """

    return (
        Path("images") / image_file_name_from_uuid(image_uuid, extension=extension)
    ).as_posix()


def table_data_file_path_from_uuid(
    artifact_dir: Path,
    table_image_uuid: str,
) -> Path:
    """
    Return the local table-data JSON path `<artifact_dir>/table_data/<table_image_uuid>.json`.
    """

    table_data_dir = artifact_dir / "table_data"
    table_data_dir.mkdir(parents=True, exist_ok=True)
    return table_data_dir / f"{table_image_uuid}.json"


def write_manifest(artifact_dir: Path, payload: dict[str, Any]) -> None:
    """
    Write a manifest.json file in the artifact directory.
    """

    manifest_path = artifact_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def stringify_endpoint_error(error: Any) -> str:
    """
    Convert error payload entries into readable strings for warnings/manifests.
    """

    if isinstance(error, str):
        return error
    try:
        return json.dumps(error, ensure_ascii=False)
    except Exception:
        return str(error)

