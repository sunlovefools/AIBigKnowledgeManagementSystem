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
  - High-level PDF orchestrator for Docling extraction.
  - Flow:
    1. Load raw layout from selected backend client.
    2. Delegate shared layout processing to `layout_processing/`.
    3. Persist manifest + return parse result.
  - Includes stage-level `print` logging.

- `layout_processing/`
  - Shared layout-processing package used by both PDF and Office extraction paths.
  - `orchestrator.py`: `process_docling_layout(...)` main loop and control flow.
  - `classification.py`: element-to-block-type classification helpers.
  - `image_export.py`: image extraction/crop helpers for local artifact files.
  - `lifecycle.py`: VLM finalization order, markdown canonicalization, output payload assembly.

- `clients/beam_client.py`
  - Beam endpoint call logic and Beam layout normalization.

- `clients/local_client.py`
  - Local Docling runtime setup, chunked conversion, and local layout normalization.

- `pptexcel_extractor.py`
  - Office (PPTX/XLSX) extraction path.
  - Reuses `layout_processing.process_docling_layout(...)` to keep layout logic consistent with PDF flow.

- `storage/local_artifacts_store.py`
  - Local artifact directory management and file path builders.
  - Writes `document.md`, `manifest.json`, and table-data JSON files.

- `utils/pdf_utils.py`
  - PDF helpers used by the pipeline (page extraction, table-shape coercion, bbox crop).

- `utils/markdown_builder.py`
  - Markdown marker helpers and structured-block assembly.

- `table_image_vlm/`
  - Existing table-image VLM pipeline package, reused by the orchestrator.

## Artifact Output

When `DOCLING_ARTIFACTS_ENABLED=true`, each run writes to:

- `backend/_local_uploads/docling_artifacts/<run_id>/document.md`
- `backend/_local_uploads/docling_artifacts/<run_id>/manifest.json`
- `backend/_local_uploads/docling_artifacts/<run_id>/images/*.png`
- `backend/_local_uploads/docling_artifacts/<run_id>/table_data/*.json` (when table-image VLM JSON exists)

