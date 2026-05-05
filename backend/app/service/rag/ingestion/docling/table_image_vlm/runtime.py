# Module purpose:
# Resolves environment-driven runtime configuration for the table-image VLM feature
# and returns the shared runtime object used by Beam/local Docling extractors.

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import constants, openrouter
from .models import TableImageVlmRuntime


def _parse_bool_env(name: str, default: bool) -> bool:
    """Parse a bool-like env var and fall back to a default on missing/invalid values."""

    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return default

def _parse_positive_int_env(
    name: str,
    default: int,
    *,
    warnings: list[str] | None = None,
) -> int:
    """Parse a positive integer env var, recording a warning when invalid."""

    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        if warnings is not None:
            warnings.append(
                f"Ignoring invalid {name}={raw!r}; expected positive integer. Using {default}."
            )
        return default
    if value <= 0:
        if warnings is not None:
            warnings.append(
                f"Ignoring invalid {name}={raw!r}; expected positive integer. Using {default}."
            )
        return default
    return value


def _load_table_image_vlm_helper_module() -> Any:
    """
    Return a module-like helper object exposing the embedded table-image helper functions.

    We return the helper module so worker/runtime code can use
    `helper_module.MODEL`, `helper_module.GEMINI_API_BASE_URL`,
    `helper_module.extract_table_json_from_image`, and
    `helper_module.extract_table_semantic_summary_from_image`.
    """

    return openrouter


def build_table_image_vlm_runtime(
    *,
    artifact_dir: Path,
    warnings: list[str],
) -> TableImageVlmRuntime | None:
    """
    Build the shared VLM runtime for a Docling parse run.

    Returns None when the feature is disabled or required config/imports are unavailable.
    """

    # Prefer Gemini key vars, keep legacy OPENROUTER_API_KEY as last-resort fallback.
    api_key = (
        (os.getenv("TABLE_IMAGE_VLM_API_KEY") or "").strip()
        or (os.getenv("GOOGLE_GEMINI_API_KEY") or "").strip()
        or (os.getenv("GEMINI_API_KEY") or "").strip()
        or (os.getenv("TABLE_SEMANTIC_LLM_API_KEY") or "").strip()
        or (os.getenv("OPENROUTER_API_KEY") or "").strip()
    )
    table_image_vlm_enabled = os.getenv("TABLE_IMAGE_VLM_ENABLED")

    print("Table-image VLM enabled:", table_image_vlm_enabled)
    if not table_image_vlm_enabled or table_image_vlm_enabled.lower() != "true":
        return None
    if not api_key:
        warnings.append(
            "Table-image VLM skipped: Gemini API key is not configured "
            "(TABLE_IMAGE_VLM_API_KEY / GOOGLE_GEMINI_API_KEY / GEMINI_API_KEY)."
        )
        return None

    try:
        helper_module = _load_table_image_vlm_helper_module()
    except Exception as exc:
        warnings.append(
            "Table-image VLM skipped: failed to initialize embedded table-image helper "
            f"({exc})."
        )
        return None

    resolved_model = (
        (os.getenv("TABLE_IMAGE_VLM_MODEL") or "").strip()
        or (os.getenv("VISION_LM_MODEL") or "").strip()
        or getattr(helper_module, "MODEL", None)
        or constants.TABLE_IMAGE_VLM_DEFAULT_MODEL
    )
    resolved_gemini_api_base_url = (
        (os.getenv("TABLE_IMAGE_VLM_GEMINI_API_BASE_URL") or "").strip()
        or (os.getenv("GOOGLE_GEMINI_API_BASE_URL") or "").strip()
        or (os.getenv("OPENROUTER_URL") or "").strip()
        or getattr(helper_module, "GEMINI_API_BASE_URL", None)
        or getattr(helper_module, "OPENROUTER_URL", None)
        or constants.TABLE_IMAGE_VLM_DEFAULT_GEMINI_API_BASE_URL
    )

    # Push env-driven model/endpoint settings into both the helper module and constants module.
    helper_module.MODEL = resolved_model
    helper_module.GEMINI_API_BASE_URL = resolved_gemini_api_base_url
    constants.MODEL = resolved_model
    constants.GEMINI_API_BASE_URL = resolved_gemini_api_base_url

    # Legacy alias retained for compatibility with older helper references/tests.
    helper_module.OPENROUTER_URL = resolved_gemini_api_base_url
    constants.OPENROUTER_URL = resolved_gemini_api_base_url

    context_blocks = _parse_positive_int_env(
        "TABLE_IMAGE_VLM_CONTEXT_BLOCKS",
        constants.TABLE_IMAGE_VLM_DEFAULT_CONTEXT_BLOCKS,
        warnings=warnings,
    )
    after_ready_blocks = _parse_positive_int_env(
        "TABLE_IMAGE_VLM_AFTER_READY_BLOCKS",
        constants.TABLE_IMAGE_VLM_DEFAULT_AFTER_READY_BLOCKS,
        warnings=warnings,
    )
    max_workers = _parse_positive_int_env(
        "TABLE_IMAGE_VLM_MAX_WORKERS",
        constants.TABLE_IMAGE_VLM_DEFAULT_MAX_WORKERS,
        warnings=warnings,
    )

    # Create the root directory early so worker threads can write per-table debug artifacts.
    artifact_root = artifact_dir / constants.TABLE_IMAGE_VLM_OUTPUT_DIRNAME
    artifact_root.mkdir(parents=True, exist_ok=True)

    return TableImageVlmRuntime(
        helper_module=helper_module,
        api_key=api_key,
        artifact_root=artifact_root,
        context_blocks=context_blocks,
        after_ready_blocks=after_ready_blocks,
        max_workers=max_workers,
    )
