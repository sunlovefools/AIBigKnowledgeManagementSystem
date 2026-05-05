"""
Runtime config for semantic table ingestion.
"""

from __future__ import annotations

import os

DEFAULT_TABLE_SEMANTIC_GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_TABLE_SEMANTIC_GEMINI_MODEL = "gemini-3.1-flash-lite-preview"


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_positive_int_env(name: str, *, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        parsed = int(raw.strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _parse_positive_float_env(name: str, *, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        parsed = float(raw.strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def is_table_semantic_ingestion_enabled() -> bool:
    """
    Feature toggle for semantic table ingestion.
    Default is ON by product decision.
    """

    return _parse_bool_env("TABLE_SEMANTIC_INGESTION_ENABLED", default=True)


def get_table_semantic_llm_url() -> str:
    """
    Semantic table ingestion uses Gemini by default.

    A caller can still provide TABLE_SEMANTIC_LLM_URL for compatibility with
    older OpenAI-compatible deployments, but this path intentionally does not
    inherit LLM_API_URL. The generic text LLM can be OpenRouter/DeepSeek while
    table extraction remains pinned to Gemini.
    """

    raw = (
        (os.getenv("TABLE_SEMANTIC_LLM_URL") or "").strip()
        or (os.getenv("TABLE_IMAGE_VLM_GEMINI_API_BASE_URL") or "").strip()
        or (os.getenv("GOOGLE_GEMINI_API_BASE_URL") or "").strip()
        or (os.getenv("GEMINI_API_BASE_URL") or "").strip()
        or DEFAULT_TABLE_SEMANTIC_GEMINI_API_BASE_URL
    )
    return raw.rstrip("/")


def get_table_semantic_llm_api_key() -> str:
    return (
        (os.getenv("TABLE_SEMANTIC_LLM_API_KEY") or "").strip()
        or (os.getenv("TABLE_IMAGE_VLM_API_KEY") or "").strip()
        or (os.getenv("GOOGLE_GEMINI_API_KEY") or "").strip()
        or (os.getenv("GEMINI_API_KEY") or "").strip()
        or (os.getenv("LLM_API_KEY") or "").strip()
        or (os.getenv("OPENROUTER_API_KEY") or "").strip()
    )


def _get_table_semantic_model(stage_env_name: str) -> str:
    return (
        (os.getenv(stage_env_name) or "").strip()
        or (os.getenv("TABLE_IMAGE_VLM_MODEL") or "").strip()
        or (os.getenv("GEMINI_MODEL") or "").strip()
        or DEFAULT_TABLE_SEMANTIC_GEMINI_MODEL
    )


def get_classifier_model() -> str:
    return _get_table_semantic_model("TABLE_SEMANTIC_CLASSIFIER_MODEL")


def get_global_model() -> str:
    return _get_table_semantic_model("TABLE_SEMANTIC_GLOBAL_MODEL")


def get_row_model() -> str:
    return _get_table_semantic_model("TABLE_SEMANTIC_ROW_MODEL")


def get_timeout_seconds() -> float:
    return _parse_positive_float_env("TABLE_SEMANTIC_TIMEOUT_S", default=60.0)


def get_max_sample_rows() -> int:
    return _parse_positive_int_env("TABLE_SEMANTIC_MAX_SAMPLE_ROWS", default=8)
