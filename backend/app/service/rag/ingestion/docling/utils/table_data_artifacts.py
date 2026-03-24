"""
Shared helpers for persisting table-image VLM TOON artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.service.rag.ingestion.docling.storage import local_artifacts_store, s3_upload


def persist_table_data_toon_artifacts(
    *,
    artifact_dir: Path | None,
    table_image_vlm_jobs: list[Any],
    resolved_file_id: str,
    file_name: str,
    warnings: list[str],
) -> None:
    """Persist TOON-wrapped table JSON artifacts locally and upload to S3 when enabled."""

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
            wrapped_payload = s3_upload.build_toon_wrapped_table_payload(
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

            try:
                s3_upload.upload_table_data_json_to_s3(
                    json_bytes=serialized.encode("utf-8"),
                    file_id=resolved_file_id,
                    table_image_uuid=job.image_artifact.image_uuid,
                    source_file_name=file_name,
                    page_no=job.page_no,
                )
            except Exception as exc:
                warnings.append(
                    "Failed to upload table-data JSON to S3 "
                    f"for table_image_uuid={job.image_artifact.image_uuid}: {exc}"
                )
        except Exception as exc:
            warnings.append(
                "Failed to convert table-image JSON to TOON "
                f"for table_image_uuid={job.image_artifact.image_uuid}: {exc}"
            )
