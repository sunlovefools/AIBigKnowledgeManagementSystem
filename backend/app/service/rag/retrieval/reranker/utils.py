from __future__ import annotations

"""Shared helper functions for reranker backends and service orchestration."""

from typing import Any

import torch


def parse_bool_env(value: str | None, default: bool = False) -> bool:
    """Parse common truthy env values (`1/true/yes/on`)."""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def detect_preferred_device() -> str:
    """
    Pick runtime inference device with accelerator preference.

    Priority: CUDA -> MPS -> CPU.
    """
    if torch.cuda.is_available():
        return "cuda"

    mps_backend = getattr(torch.backends, "mps", None)
    if mps_backend is not None and mps_backend.is_available():
        return "mps"

    return "cpu"


def clear_device_cache(device: str) -> None:
    """Release allocator cache for the selected accelerator backend."""
    if device == "cuda" and torch.cuda.is_available():
        torch.cuda.empty_cache()
        return

    if device == "mps":
        mps_module = getattr(torch, "mps", None)
        if mps_module is not None and hasattr(mps_module, "empty_cache"):
            mps_module.empty_cache()


def ensure_tokenizer_padding(tokenizer: Any, model_config: Any = None) -> bool:
    """
    Ensure tokenizer has a pad token and propagate pad id into model config.

    Fallback sequence for missing pad token: `eos -> sep -> unk`.

    Returns:
        bool: True if a pad token id is available after normalization.
    """
    if tokenizer is None:
        return False

    pad_token_id = getattr(tokenizer, "pad_token_id", None)
    if pad_token_id is None:
        for base_name in ("eos", "sep", "unk"):
            token = getattr(tokenizer, f"{base_name}_token", None)
            token_id = getattr(tokenizer, f"{base_name}_token_id", None)
            if token is None or token_id is None:
                continue

            try:
                tokenizer.pad_token = token
                tokenizer.pad_token_id = token_id
                break
            except Exception:
                continue

    pad_token_id = getattr(tokenizer, "pad_token_id", None)
    if pad_token_id is None:
        return False

    if model_config is not None and getattr(model_config, "pad_token_id", None) is None:
        model_config.pad_token_id = pad_token_id

    return True


def to_pylist(value: Any) -> Any:
    """
    Best-effort conversion from tensor-like objects to plain Python lists.

    Works with torch tensors and lightweight test doubles used in unit tests.
    """
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "float"):
        value = value.float()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        return value.tolist()
    return value
