# Module purpose:
# Defines the dataclasses used by the table-image VLM pipeline for shared runtime
# configuration, queued job state, and per-worker execution results.

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.service.rag.ingestion.docling.models import ExtractedImageArtifact


@dataclass
class TableImageVlmRuntime:
    """Resolved runtime configuration used to submit table-image VLM jobs."""

    helper_module: Any
    api_key: str
    artifact_root: Path
    context_blocks: int
    after_ready_blocks: int
    max_workers: int


@dataclass
class TableImageVlmWorkerResult:
    """Outcome of one table-image VLM worker (JSON extraction + summary generation)."""

    json_ok: bool
    summary_ok: bool
    summary_text: str | None = None
    json_path: str | None = None
    summary_path: str | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class TableImageVlmJob:
    """
    Tracks one queued fallback table-image block and its async processing state.
    """

    image_artifact: ExtractedImageArtifact
    table_index: int
    page_no: int | None
    block_index: int
    summary_placeholder: str
    output_dir: Path
    json_rel_path: str
    submitted: bool = False
    future: Future | None = None
    context_before: str = ""
    context_after: str = ""
    result: TableImageVlmWorkerResult | None = None
