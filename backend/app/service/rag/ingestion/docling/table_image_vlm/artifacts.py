# Module purpose:
# Provides artifact path builders and atomic file-writing helpers for per-table VLM
# outputs (JSON, summaries, status files) and markdown placeholder markers.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import TABLE_IMAGE_VLM_OUTPUT_DIRNAME
from .models import TableImageVlmJob


def table_image_vlm_summary_placeholder(image_uuid: str) -> str:
    """Return a unique placeholder marker that will later be replaced with the VLM summary."""

    return f"<!-- table-image-vlm-summary-slot: {image_uuid} -->"


def _table_image_vlm_dir_name(*, table_index: int, image_uuid: str) -> str:
    """Build the per-table VLM artifact folder name."""

    return f"table-{table_index}-{image_uuid}"


def table_image_vlm_output_dir(
    artifact_dir: Path,
    *,
    table_index: int,
    image_uuid: str,
) -> Path:
    """Return the output directory path for a table-image VLM job under the run artifact dir."""

    return artifact_dir / TABLE_IMAGE_VLM_OUTPUT_DIRNAME / _table_image_vlm_dir_name(
        table_index=table_index,
        image_uuid=image_uuid,
    )


def table_image_vlm_json_rel_path(*, table_index: int, image_uuid: str) -> str:
    """Return a markdown-friendly relative path to the extracted JSON debug file."""

    rel_path = (
        Path(TABLE_IMAGE_VLM_OUTPUT_DIRNAME)
        / _table_image_vlm_dir_name(table_index=table_index, image_uuid=image_uuid)
        / "output.json"
    )
    return str(rel_path).replace("\\", "/")


def _write_text_file(path: Path, content: str) -> None:
    """Atomically write a UTF-8 text file (best-effort via temp file replace)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)
    except (FileNotFoundError, OSError):
        # Windows path-length/path-resolution edge cases can fail for temp-path writes.
        # Fall back to direct write so summary/json artifacts are still persisted.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _write_json_file(path: Path, payload: Any) -> None:
    """Atomically write a JSON file for debug/status artifacts."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    serialized = json.dumps(payload, indent=2, ensure_ascii=False)
    try:
        tmp_path.write_text(serialized, encoding="utf-8")
        tmp_path.replace(path)
    except (FileNotFoundError, OSError):
        # Keep VLM flow resilient when temporary file paths exceed OS constraints.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized, encoding="utf-8")
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _prepare_table_image_vlm_worker_paths(job: TableImageVlmJob) -> tuple[Path, Path, Path]:
    """
    Ensure the per-job output directory exists and return the key artifact paths.

    Returns: (output_dir, json_path, summary_path)
    """

    output_dir = job.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    return (
        output_dir,
        output_dir / "output.json",
        output_dir / "semantic_summary.txt",
    )
