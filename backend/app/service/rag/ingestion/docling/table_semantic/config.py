"""
Runtime config for semantic table ingestion.
"""

from __future__ import annotations

import os

from app.service.llm_env import (
    DEFAULT_LLM_MODEL,
    resolve_llm_api_key,
    resolve_llm_api_url,
    resolve_llm_model,
)


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
    OpenAI-compatible chat completions endpoint.
    """

    return resolve_llm_api_url(os.getenv("TABLE_SEMANTIC_LLM_URL"))


def get_table_semantic_llm_api_key() -> str:
    return resolve_llm_api_key(os.getenv("TABLE_SEMANTIC_LLM_API_KEY")) or ""


def get_classifier_model() -> str:
    return resolve_llm_model(
        os.getenv("TABLE_SEMANTIC_CLASSIFIER_MODEL"),
        default=DEFAULT_LLM_MODEL,
    )


def get_global_model() -> str:
    return resolve_llm_model(
        os.getenv("TABLE_SEMANTIC_GLOBAL_MODEL"),
        default=DEFAULT_LLM_MODEL,
    )


def get_row_model() -> str:
    return resolve_llm_model(
        os.getenv("TABLE_SEMANTIC_ROW_MODEL"),
        default=DEFAULT_LLM_MODEL,
    )


def get_timeout_seconds() -> float:
    return _parse_positive_float_env("TABLE_SEMANTIC_TIMEOUT_S", default=60.0)


def get_max_sample_rows() -> int:
    return _parse_positive_int_env("TABLE_SEMANTIC_MAX_SAMPLE_ROWS", default=8)
