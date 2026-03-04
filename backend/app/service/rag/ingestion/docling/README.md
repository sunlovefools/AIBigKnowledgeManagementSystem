# Docling Package

This package contains the Docling-based PDF extraction flow used by preview and ingestion routes.

## Module Map

- `__init__.py`
  - Public facade for Docling extraction.
  - Chooses backend (`beam` or `local`) and exposes public constants/models.

- `config.py`
  - Shared constants and env-backed toggles.
  - Includes backend selection and artifact enablement.

- `models.py`
  - Shared Pydantic models returned by Docling parsing.

- `pipeline.py`
  - Single orchestrator for Docling extraction.
  - Flow:
    1. Load raw layout from selected backend client.
    2. Iterate layout items once.
    3. Export picture/table images to local artifacts.
    4. Upload images to S3 when enabled.
    5. Queue table image VLM jobs and inject markdown placeholders.
    6. Finalize markdown + manifest and return parse result.
  - Includes stage-level `print` logging.

- `clients/beam_client.py`
  - Beam endpoint call logic and Beam layout normalization.

- `clients/local_client.py`
  - Local Docling runtime setup, chunked conversion, and local layout normalization.

- `storage/local_artifacts_store.py`
  - Local artifact directory management and file path builders.
  - Writes `document.md`, `manifest.json`, and table-data JSON files.

- `storage/s3_upload.py`
  - S3 upload helpers for extracted images and table-data JSON artifacts.
  - Handles TOON payload wrapping for table-data export.

- `utils/pdf_utils.py`
  - PDF helpers used by the pipeline (page extraction, table-shape coercion, bbox crop).

- `utils/markdown_builder.py`
  - Markdown marker helpers and structured-block assembly.

- `table_image_vlm/`
  - Existing table-image VLM pipeline package, reused by the orchestrator.

## Artifact Output

When `DOCLING_ARTIFACTS_ENABLED=true`, each run writes to:

- `backend/_local_uploads/docling_previews/<run_id>/document.md`
- `backend/_local_uploads/docling_previews/<run_id>/manifest.json`
- `backend/_local_uploads/docling_previews/<run_id>/images/*.png`
- `backend/_local_uploads/docling_previews/<run_id>/table_data/*.json` (when table-image VLM JSON exists)

## S3 Uploads

- Toggle: `AWS_S3_UPLOAD_ENABLED` (`true`/`false`).
- Upload helpers are best-effort; failures are recorded in warnings and image artifact status.

