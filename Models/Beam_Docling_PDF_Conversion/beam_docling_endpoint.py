"""
Beam-hosted Docling PDF conversion endpoint.

Purpose:
- Offload PDF conversion to a Beam GPU worker so local machines do not need to
  run the full Docling pipeline.
- Provide a stable JSON response that clients can use for markdown generation,
  local image cropping, and downstream post-processing.

Quick start (run from this folder):
1. `uv venv .venv`
2. `uv sync`
3. `uv run beam serve beam_docling_endpoint.py:convert_pdf`

Notes:
- Use the Beam URL printed by the current `beam serve` session in your client.
- This endpoint returns bbox/page metadata so clients can crop images locally.
"""

from __future__ import annotations

import base64
import binascii
import io
import math
import os
import threading
from dataclasses import asdict, is_dataclass
from typing import Any

from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import (
    AcceleratorOptions,
    ThreadedPdfPipelineOptions,
)
from docling_core.types.doc import PictureItem, TableItem
from docling_core.types.io import DocumentStream
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from beam import Image, endpoint


# Runtime defaults (all overridable via env vars in Beam deployment).
# These are tuned for the requested single-GPU worker (RTX4090) + 2 CPU cores.
DEFAULT_LAYOUT_BATCH = 32
DEFAULT_TABLE_BATCH = 32
MIN_BATCH_SIZE = 8
BEAM_CPU_THREADS = 2
BEAM_TIMEOUT_SECONDS = 600
MAX_SERVER_FILE_SIZE_MB = 25
MAX_NUM_PAGES = 10000
IMAGES_SCALE = 2.0
RESPONSE_WARN_BYTES = 8 * 1024 * 1024  # warn if inline image payload exceeds 8 MiB (base64-encoded PNGs can be large)

# Cache converters by (layout_batch_size, table_batch_size) so warm Beam workers
# can reuse initialized models across requests and retries.
_CONVERTER_CACHE: dict[tuple[int, int], DocumentConverter] = {}
_CONVERTER_CACHE_LOCK = threading.Lock()


class ConvertPdfRequest(BaseModel):
    """Validated request schema for the Beam `convert_pdf` endpoint."""

    # Ignore unknown top-level fields so clients can add metadata safely.
    model_config = ConfigDict(extra="ignore")

    filename: str
    file_b64: str
    include_conversion_dump: bool = True
    max_file_size_mb: int | None = None
    options: dict[str, Any] | None = None

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        """Ensure the uploaded filename is present and looks like a PDF filename."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("filename is required and must be a non-empty string.")
        if not value.lower().endswith(".pdf"):
            raise ValueError("filename must end with .pdf")
        return value

    @field_validator("file_b64")
    @classmethod
    def _validate_file_b64(cls, value: str) -> str:
        """Ensure the request includes a non-empty base64 string payload."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("file_b64 is required and must be a non-empty base64 string.")
        return value

    @field_validator("include_conversion_dump")
    @classmethod
    def _validate_include_conversion_dump(cls, value: bool) -> bool:
        """Require an explicit boolean for the conversion dump inclusion flag."""
        if not isinstance(value, bool):
            raise ValueError("include_conversion_dump must be a boolean when provided.")
        return value

    @field_validator("max_file_size_mb")
    @classmethod
    def _validate_max_file_size_mb(cls, value: int | None) -> int | None:
        """Validate optional client size hint and enforce positive integer values."""
        if value is None:
            return value
        if not isinstance(value, int):
            raise ValueError("max_file_size_mb must be an integer when provided.")
        if value <= 0:
            raise ValueError("max_file_size_mb must be > 0 when provided.")
        return value

    @field_validator("options", mode="before")
    @classmethod
    def _coerce_options(cls, value: Any) -> dict[str, Any] | None:
        """Coerce malformed `options` payloads to an empty dict for compatibility."""
        # Keep backward-compatible behavior: malformed options are ignored.
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        return {}


def _build_beam_image() -> Image:
    """Build the Beam container image definition with system and Python dependencies.

    The system packages include OpenCV/runtime shared libraries (such as
    `libGL.so.1`) required by Docling dependencies at runtime.
    """
    # Beam will install packages in the container. If you need a CUDA-pinned torch wheel,
    # replace this with a custom image / commands in your deployment environment.

    # This image will be cached after first request, so subsequent requests and retries will be faster.
    return (
        Image(python_version="python3.11")
        .add_commands(
            [
                "apt-get update && apt-get install -y --no-install-recommends "
                "libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 && "
                "rm -rf /var/lib/apt/lists/*"
            ]
        )
        .add_python_packages(
            [
                "docling>=2.74.0",
                "hf-xet>=1.2.0",
                "pillow>=12.1.1",
            ]
        )
    )


def _startup_context() -> dict[str, Any]:
    """Warm the converter cache during worker startup and return startup notes."""
    # Best-effort warmup to reduce first-request latency on warm containers.
    notes: list[str] = []
    try:
        _get_or_create_converter(DEFAULT_LAYOUT_BATCH, DEFAULT_TABLE_BATCH)
        notes.append("Docling converter warmup completed.")
    except Exception as exc:  # pragma: no cover - startup behavior depends on remote env
        notes.append(f"Warmup skipped/failed: {exc}")
    return {"startup_notes": notes}


@endpoint(
    name="docling-convert-pdf",
    cpu=2,
    gpu="RTX4090",
    memory="10Gi",
    timeout=BEAM_TIMEOUT_SECONDS,
    workers=1,
    keep_warm_seconds=int(os.getenv("BEAM_KEEP_WARM_SECONDS", "60")),
    image=_build_beam_image(),
    on_start=_startup_context,
)
def convert_pdf(**inputs: Any) -> dict[str, Any]:
    """Convert a base64-encoded PDF with Docling and return a JSON-safe response.

    This is the Beam handler entrypoint. It validates the request payload,
    enforces size limits, runs Docling conversion with CUDA OOM retry fallback,
    serializes the result into transport-safe JSON, and returns item metadata
    (including page number and bounding boxes) for client-side post-processing.
    """
    # Flow:
    # 1) Validate request + size limits
    # 2) Decode base64 PDF bytes
    # 3) Convert full document with adaptive batch fallback on CUDA OOM
    # 4) Serialize ConversionResult + ordered items (with optional item images)
    # 5) Return JSON-safe response for client-side markdown/image handling
    server_notes: list[str] = []

    try:
        request = _parse_request(inputs)
    except ValueError as exc:
        return _error_response(
            code="INVALID_REQUEST",
            message=str(exc),
            server_notes=server_notes,
        )

    options = request.options or {}

    # Allow client to request a lower limit, but never exceed the server hard cap.
    max_file_size_mb = _resolve_file_size_limit_mb(request, options)

    try:
        pdf_bytes = base64.b64decode(request.file_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        return _error_response(
            code="INVALID_BASE64",
            message=f"file_b64 is not valid base64: {exc}",
            server_notes=server_notes,
            meta={"filename": request.filename},
        )

    if not pdf_bytes:
        return _error_response(
            code="EMPTY_FILE",
            message="Decoded PDF payload is empty.",
            server_notes=server_notes,
            meta={"filename": request.filename},
        )

    file_size_bytes = len(pdf_bytes)
    max_file_size_bytes = max_file_size_mb * 1024 * 1024
    if file_size_bytes > max_file_size_bytes:
        return _error_response(
            code="FILE_TOO_LARGE",
            message=(
                f"File size {file_size_bytes} bytes exceeds server limit "
                f"{max_file_size_bytes} bytes ({max_file_size_mb} MB)."
            ),
            server_notes=server_notes,
            meta={
                "filename": request.filename,
                "file_size_bytes": file_size_bytes,
                "max_file_size_bytes": max_file_size_bytes,
            },
        )

    include_conversion_dump = bool(request.include_conversion_dump)
    # Inline image payloads are disabled by design to keep response sizes small.
    # Clients can crop from the source PDF using `page_no` + `bbox`.
    include_item_images = False

    result = None
    # Precompute retry ladder (e.g., 32 -> 16 -> 8) for OOM fallback.
    batch_attempts = _batch_attempts(DEFAULT_LAYOUT_BATCH, DEFAULT_TABLE_BATCH, MIN_BATCH_SIZE)
    retry_count = 0
    batch_used: dict[str, int] | None = None
    last_exception: Exception | None = None

    for attempt_index, (layout_batch_size, table_batch_size) in enumerate(batch_attempts):
        try:
            # Recreate DocumentStream each attempt so the underlying BytesIO is fresh.
            doc_stream = DocumentStream(name=request.filename, stream=io.BytesIO(pdf_bytes))
            converter = _get_or_create_converter(layout_batch_size, table_batch_size)
            result = converter.convert(
                doc_stream,
                raises_on_error=False,
                max_num_pages=MAX_NUM_PAGES,
                max_file_size=max_file_size_bytes,
            )
            batch_used = {
                "layout_batch_size": layout_batch_size,
                "table_batch_size": table_batch_size,
            }
            retry_count = attempt_index
            break
        except Exception as exc:
            last_exception = exc
            # Only auto-retry for memory pressure. Other failures return immediately.
            if _looks_like_cuda_oom(exc) and attempt_index < len(batch_attempts) - 1:
                retry_count = attempt_index + 1
                server_notes.append(
                    "CUDA OOM detected; retrying with smaller batches "
                    f"(attempt {attempt_index + 2}/{len(batch_attempts)})."
                )
                continue
            return _error_response(
                code="CONVERSION_EXCEPTION",
                message=str(exc),
                server_notes=server_notes,
                meta={
                    "filename": request.filename,
                    "file_size_bytes": file_size_bytes,
                    "batch_attempts": [
                        {"layout_batch_size": lb, "table_batch_size": tb}
                        for lb, tb in batch_attempts
                    ],
                    "retry_count": retry_count,
                    "last_exception_type": type(exc).__name__,
                },
            )

    if result is None:
        return _error_response(
            code="CONVERSION_FAILED",
            message=str(last_exception) if last_exception else "Docling conversion failed.",
            server_notes=server_notes,
            meta={"filename": request.filename, "file_size_bytes": file_size_bytes},
        )

    conversion_result_dump = None
    if include_conversion_dump:
        try:
            # JSON-safe representation of the full ConversionResult object.
            conversion_result_dump = _safe_jsonable(result.model_dump(mode="json"))
        except Exception as exc:
            server_notes.append(f"conversion_result_dump unavailable: {exc}")

    # Ordered item extraction preserves iterate_items() sequence so the client can
    # reconstruct markdown/image handling without positional drift.
    ordered_items, image_payload_bytes = _extract_ordered_items(
        result=result,
        include_item_images=include_item_images,
        server_notes=server_notes,
    )

    if image_payload_bytes > RESPONSE_WARN_BYTES:
        server_notes.append(
            "Large inline image payload detected "
            f"({image_payload_bytes} bytes base64) and may increase latency."
        )

    status_value = getattr(result.status, "value", str(result.status))
    resp = {
        "ok": status_value not in {"failure", "skipped"},
        "status": status_value,
        "errors": [_safe_jsonable(e) for e in getattr(result, "errors", [])],
        "meta": {
            "filename": request.filename,
            "file_size_bytes": file_size_bytes,
            "docling_version": _extract_docling_version(result),
            "batch_config_used": batch_used,
            "device": "cuda",
            "retry_count": retry_count,
            "max_num_pages": MAX_NUM_PAGES,
            "include_conversion_dump": include_conversion_dump,
            "include_item_images": False,
        },
        "conversion_result_dump": conversion_result_dump,
        "ordered_items": ordered_items,
        "server_notes": server_notes,
    }
    if resp["ok"]:
        print(
            "convert_pdf success "
            f"filename={request.filename} status={status_value} "
            f"ordered_items={len(ordered_items)} retry_count={retry_count}"
        )
    return _safe_jsonable(resp)


def _parse_request(inputs: dict[str, Any]) -> ConvertPdfRequest:
    """Validate raw handler kwargs into a typed request model.

    Converts Pydantic validation errors into a compact `ValueError` message so
    the endpoint can return a stable `INVALID_REQUEST` error envelope.
    """
    # Centralized Pydantic validation keeps the request contract maintainable
    # as the endpoint evolves (and avoids duplicating manual checks).
    try:
        return ConvertPdfRequest.model_validate(inputs)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else None
        if first is None:
            raise ValueError(str(exc)) from exc
        loc = ".".join(str(part) for part in first.get("loc", ()))
        msg = first.get("msg", str(exc))
        raise ValueError(f"{loc}: {msg}" if loc else msg) from exc


def _resolve_file_size_limit_mb(request: ConvertPdfRequest, options: dict[str, Any]) -> int:
    """Resolve the effective file-size limit by combining request and options hints.

    Clients may request a smaller limit than the server default, but they can
    never increase the server-side hard cap.
    """
    # Request-level limit can only tighten the server cap, never increase it.
    client_limit = request.max_file_size_mb
    option_limit = options.get("max_file_size_mb")

    for candidate in (client_limit, option_limit):
        if candidate is None:
            continue
        if isinstance(candidate, int) and candidate > 0:
            return min(candidate, MAX_SERVER_FILE_SIZE_MB)

    return MAX_SERVER_FILE_SIZE_MB


def _batch_attempts(layout_batch: int, table_batch: int, min_batch: int) -> list[tuple[int, int]]:
    """Build a descending batch-size retry ladder for CUDA OOM recovery.

    Example: `(32, 32)` -> `(16, 16)` -> `(8, 8)`, deduplicated if the starting
    sizes are already at or below the minimum.
    """
    # Build descending batch-size attempts for OOM recovery.
    # Example: (32,32) -> (16,16) -> (8,8)
    attempts: list[tuple[int, int]] = []
    lb = max(1, layout_batch)
    tb = max(1, table_batch)
    while True:
        attempts.append((lb, tb))
        if lb <= min_batch and tb <= min_batch:
            break
        lb = max(min_batch, max(1, lb // 2))
        tb = max(min_batch, max(1, tb // 2))
        if attempts and attempts[-1] == (lb, tb):
            break
    # Deduplicate in case starting batch is already <= min_batch
    deduped: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for item in attempts:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def _docling_pipeline_options(layout_batch_size: int, table_batch_size: int) -> ThreadedPdfPipelineOptions:
    """Create Docling pipeline options for a single conversion attempt.

    All retries share the same feature configuration; only the layout/table
    batch sizes change to reduce GPU memory pressure after OOM failures.
    """
    # Centralized Docling pipeline setup so all retries stay identical except for
    # the batch sizes used to control GPU memory pressure.
    pipeline_options = ThreadedPdfPipelineOptions()
    pipeline_options.accelerator_options = AcceleratorOptions(
        device="cuda",
        num_threads=BEAM_CPU_THREADS,
        cuda_use_flash_attention2=False,
    )
    pipeline_options.do_ocr = False
    pipeline_options.do_table_structure = True
    pipeline_options.generate_table_images = True
    pipeline_options.generate_picture_images = True
    pipeline_options.generate_page_images = False
    pipeline_options.do_chart_extraction = False
    pipeline_options.do_formula_enrichment = False
    pipeline_options.do_code_enrichment = False
    pipeline_options.do_picture_description = False
    pipeline_options.do_picture_classification = False
    pipeline_options.images_scale = IMAGES_SCALE
    pipeline_options.layout_batch_size = layout_batch_size
    pipeline_options.table_batch_size = table_batch_size
    return pipeline_options


def _get_or_create_converter(layout_batch_size: int, table_batch_size: int) -> DocumentConverter:
    """Return a cached `DocumentConverter` for the given batch-size configuration.

    Converter/model initialization is expensive, so warm Beam workers reuse
    instances keyed by `(layout_batch_size, table_batch_size)`.
    """
    # Reuse converter instances because model initialization is expensive.
    # The cache key includes batch sizes because retries change those values.
    key = (layout_batch_size, table_batch_size)
    cached = _CONVERTER_CACHE.get(key)
    if cached is not None:
        return cached

    with _CONVERTER_CACHE_LOCK:
        cached = _CONVERTER_CACHE.get(key)
        if cached is not None:
            return cached

        pipeline_options = _docling_pipeline_options(layout_batch_size, table_batch_size)
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        _CONVERTER_CACHE[key] = converter
        return converter


def _extract_ordered_items(
    *,
    result: Any,
    include_item_images: bool,
    server_notes: list[str],
) -> tuple[list[dict[str, Any]], int]:
    """Serialize Docling document items into an ordered, response-friendly list.

    Returns:
    - ordered item records preserving `iterate_items()` order
    - total base64 image payload bytes (for warning/observability)

    When inline images are disabled, `image_png_b64` remains `None` and clients
    should use `page_no` + `bbox` for local cropping.
    """
    # Iterate once through the Docling document and build a response-friendly list
    # that preserves order, per-item metadata, and optional inline images.
    ordered: list[dict[str, Any]] = []
    image_payload_bytes = 0

    document = getattr(result, "document", None)
    if document is None:
        server_notes.append("Conversion result has no document field.")
        return ordered, image_payload_bytes

    for seq, (element, level) in enumerate(document.iterate_items()):
        item_type = type(element).__name__
        item_record: dict[str, Any] = {
            "seq": seq,
            "level": int(level),
            "item_type": item_type,
            "item_ref": _extract_item_ref(element, seq),
            "page_no": _extract_page_no(element),
            "bbox": _extract_bbox(element),
            "table_info": None,
            "image_png_b64": None,
            "image_error": None,
            "item_dump": _safe_item_dump(element),
        }

        if isinstance(element, TableItem):
            table_data = getattr(element, "data", None)
            item_record["table_info"] = {
                "num_rows": getattr(table_data, "num_rows", None),
                "num_cols": getattr(table_data, "num_cols", None),
            }

        if include_item_images and isinstance(element, (PictureItem, TableItem)):
            try:
                img = element.get_image(document)
                if img is not None:
                    # Inline PNG keeps the response self-contained for client-side save/serialization.
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    png_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                    item_record["image_png_b64"] = png_b64
                    image_payload_bytes += len(png_b64)
            except Exception as exc:
                item_record["image_error"] = str(exc)

        ordered.append(item_record)

    return ordered, image_payload_bytes


def _safe_item_dump(element: Any) -> dict[str, Any] | None:
    """Best-effort JSON-safe serialization for a single Docling document element."""
    # Best-effort serialization of individual Docling items; failure is non-fatal.
    if hasattr(element, "model_dump"):
        try:
            return _safe_jsonable(element.model_dump(mode="json"))
        except Exception:
            try:
                return _safe_jsonable(element.model_dump())
            except Exception:
                return None
    return None


def _extract_item_ref(element: Any, seq: int) -> str:
    """Extract a stable-ish item identifier, falling back to sequence index."""
    # Prefer a native stable identifier if Docling exposes one.
    for attr in ("self_ref", "ref", "id"):
        value = getattr(element, attr, None)
        if value is None:
            continue
        if isinstance(value, (str, int)):
            return str(value)
        text = str(value)
        if text:
            return text
    return f"seq:{seq}"


def _extract_page_no(element: Any) -> int | None:
    """Extract the first provenance page number from a Docling element, if present."""
    # Many Docling items carry provenance (`prov`) with page references.
    prov = getattr(element, "prov", None)
    if not prov:
        return None
    first = prov[0]
    page_no = getattr(first, "page_no", None)
    return int(page_no) if isinstance(page_no, int) else None


def _extract_bbox(element: Any) -> dict[str, Any] | None:
    """Extract and normalize the first provenance bounding box into JSON-safe form.

    Supports Pydantic models, dataclasses, and plain Python objects; falls back
    to string representations when structured serialization is unavailable.
    """
    # Normalize bbox into a JSON-safe dict so clients can use coordinates directly.
    prov = getattr(element, "prov", None)
    if not prov:
        return None
    first = prov[0]
    bbox = getattr(first, "bbox", None)
    if bbox is None:
        return None

    if hasattr(bbox, "model_dump"):
        try:
            return bbox.model_dump(mode="json")
        except Exception:
            try:
                return _safe_jsonable(bbox.model_dump())
            except Exception:
                return {"repr": str(bbox)}

    if is_dataclass(bbox):
        try:
            return _safe_jsonable(asdict(bbox))
        except Exception:
            return {"repr": str(bbox)}

    if hasattr(bbox, "__dict__"):
        try:
            return _safe_jsonable(vars(bbox))
        except Exception:
            return {"repr": str(bbox)}

    raw_bbox = _safe_jsonable(bbox)
    if isinstance(raw_bbox, dict):
        return raw_bbox
    return {"value": raw_bbox}


def _safe_jsonable(obj: Any) -> Any:
    """Recursively convert arbitrary objects into JSON-safe values.

    Handles common Docling/Pydantic/dataclass objects, bytes (base64 encoded),
    containers, and non-finite floats (`NaN`/`Infinity`) which are converted to
    `None` so strict JSON encoders accept the response.
    """
    # Generic recursive fallback used for response serialization.
    # Converts Pydantic models/dataclasses/bytes into JSON-friendly values.
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj

    if isinstance(obj, float):
        # Starlette JSONResponse uses strict JSON encoding and rejects NaN/Infinity.
        return obj if math.isfinite(obj) else None

    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode("ascii")

    if isinstance(obj, dict):
        return {str(k): _safe_jsonable(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple, set)):
        return [_safe_jsonable(v) for v in obj]

    if hasattr(obj, "model_dump"):
        try:
            return _safe_jsonable(obj.model_dump(mode="json"))
        except Exception:
            try:
                return _safe_jsonable(obj.model_dump())
            except Exception:
                return str(obj)

    if is_dataclass(obj):
        try:
            return _safe_jsonable(asdict(obj))
        except Exception:
            return str(obj)

    if hasattr(obj, "__dict__"):
        try:
            return _safe_jsonable(vars(obj))
        except Exception:
            return str(obj)

    return str(obj)


def _extract_docling_version(result: Any) -> Any:
    """Read and JSON-sanitize the Docling version metadata from a result object."""
    version = getattr(result, "version", None)
    return _safe_jsonable(version)


def _looks_like_cuda_oom(exc: Exception) -> bool:
    """Heuristically detect CUDA OOM-like exceptions from error message text."""
    # String matching is used because low-level CUDA/runtime errors vary by stack.
    text = str(exc).lower()
    oom_markers = (
        "cuda out of memory",
        "cuda error: out of memory",
        "cublas_status_alloc_failed",
        "outofmemoryerror",
    )
    return any(marker in text for marker in oom_markers)


def _error_response(
    *,
    code: str,
    message: str,
    server_notes: list[str],
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the standardized JSON error envelope returned by the endpoint.

    This keeps client error handling predictable across request validation,
    decoding failures, and conversion/runtime exceptions.
    """
    # Consistent error envelope so the client can handle failures predictably.
    return {
        "ok": False,
        "status": "error",
        "error_code": code,
        "error_message": message,
        "errors": [],
        "meta": meta or {},
        "conversion_result_dump": None,
        "ordered_items": [],
        "server_notes": server_notes,
    }
