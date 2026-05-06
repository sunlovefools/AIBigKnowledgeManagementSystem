"""
Shared helpers for persisting table-image VLM TOON artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.service.rag.ingestion.docling.storage import local_artifacts_store


def _build_toon_wrapped_table_payload(
    *,
    extracted_table_json: Any,
    file_id: str,
    page_no: int | None,
) -> dict[str, Any]:
    """Convert extracted table JSON into TOON and wrap in the expected schema."""

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


def persist_table_data_toon_artifacts(
    *,
    artifact_dir: Path | None,
    table_image_vlm_jobs: list[Any],
    resolved_file_id: str,
    warnings: list[str],
) -> None:
    """Persist TOON-wrapped table JSON artifacts locally."""

    if artifact_dir is None or not table_image_vlm_jobs:
        return

    for job in table_image_vlm_jobs:
        if job.result is not None and job.result.json_path:
            extracted_json_path = Path(job.result.json_path)
        else:
            extracted_json_path = job.output_dir / "output.json"

        if not extracted_json_path.exists():
            continue

        try:
            extracted_payload = json.loads(extracted_json_path.read_text(encoding="utf-8"))
            wrapped_payload = _build_toon_wrapped_table_payload(
                extracted_table_json=extracted_payload,
                file_id=resolved_file_id,
                page_no=job.page_no,
            )
            table_data_path = local_artifacts_store.table_data_file_path_from_uuid(
                artifact_dir,
                job.image_artifact.image_uuid,
            )
            serialized = json.dumps(wrapped_payload, indent=2, ensure_ascii=False)
            table_data_path.write_text(serialized, encoding="utf-8")
        except Exception as exc:
            warnings.append(
                "Failed to convert table-image JSON to TOON "
                f"for table_image_uuid={job.image_artifact.image_uuid}: {exc}"
            )
