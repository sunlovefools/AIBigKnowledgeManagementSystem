from __future__ import annotations

"""
Service facade for reranker backends.

This module is the only orchestration layer that callers use. It keeps the
public API stable (`ZeRankerService`) while delegating model-specific behavior
to pluggable backend implementations that satisfy `RerankerInterface`.
"""

import asyncio
import gc
import os
from typing import Any, List, Tuple

from .bge_reranker import BGEReranker
from .interface import RerankerInterface
from .qwen_reranker import QwenReranker
from .utils import clear_device_cache, detect_preferred_device, normalize_env_value, parse_bool_env

DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

SUPPORTED_RERANKER_MODELS: dict[str, dict[str, Any]] = {
    "BAAI/bge-reranker-v2-m3": {
        "backend": "cross_encoder",
        "factory": BGEReranker,
        "kwargs": {"model_kwargs": {"torch_dtype": "auto"}},
    },
    "Qwen/Qwen3-Reranker-0.6B": {
        "backend": "qwen_causal_lm",
        "factory": QwenReranker,
        "kwargs": {"model_kwargs": {"torch_dtype": "auto"}, "trust_remote_code": True},
    },
}


class ZeRankerService:
    """
    Reranker service facade.
    Public contract stays stable while model-specific logic lives in separate classes.
    """

    def __init__(self, model_name: str | None = None):
        """
        Initialize reranker backend selected by env or constructor override.

        Env vars:
        - RERANKER_MODEL
        - RERANKER_SWAP_TO_RAM
        """
        configured_model_raw = model_name or os.getenv("RERANKER_MODEL", DEFAULT_RERANKER_MODEL)
        configured_model = normalize_env_value(configured_model_raw) or DEFAULT_RERANKER_MODEL
        if configured_model not in SUPPORTED_RERANKER_MODELS:
            print(
                f"[RERANKER] Unsupported RERANKER_MODEL={configured_model!r}. "
                f"Falling back to {DEFAULT_RERANKER_MODEL!r}."
            )
            configured_model = DEFAULT_RERANKER_MODEL

        self.model_name = configured_model
        # Service-level policy: backends stay on CPU between requests when enabled.
        self.swap_to_ram = parse_bool_env(os.getenv("RERANKER_SWAP_TO_RAM"), default=False)
        self.preferred_device = detect_preferred_device()
        self.active_device = (
            "cpu" if self.swap_to_ram and self.preferred_device != "cpu" else self.preferred_device
        )
        self._inference_lock = asyncio.Lock()

        model_def = SUPPORTED_RERANKER_MODELS[self.model_name]
        self.backend = str(model_def["backend"])
        factory = model_def["factory"]
        kwargs = dict(model_def.get("kwargs", {}))

        print(
            f"[RERANKER] Loading model={self.model_name}, backend={self.backend}, "
            f"preferred_device={self.preferred_device}, initial_device={self.active_device}, "
            f"swap_to_ram={self.swap_to_ram}"
        )

        try:
            self._engine: RerankerInterface = factory(
                self.model_name,
                device=self.active_device,
                **kwargs,
            )
            print(f"[RERANKER] Model {self.model_name!r} loaded successfully.")
        except Exception as error:
            print(f"[RERANKER] Failed to load model {self.model_name!r}: {error}")
            raise error

    def _set_model_device(self, target_device: str) -> None:
        """Move active backend model to target device and track state."""
        if target_device == self.active_device:
            return
        self._engine.set_device(target_device)
        self.active_device = target_device

    async def rerank_documents(
        self,
        query: str,
        documents: List[str],
        top_k: int = 5,
    ) -> List[Tuple[str, float]]:
        """
        Rerank documents for a query and return top-k `(document, score)` pairs.

        Runtime behavior:
        - serializes inference/device moves with an async lock
        - retries once with batch_size=1 for known padding-related errors
        - fails soft (original order, score=0.0) on unrecoverable errors
        - offloads model back to CPU after inference when configured
        """
        if not documents:
            return []

        query_documents = [[query, doc] for doc in documents]

        async with self._inference_lock:
            try:
                if self.preferred_device != "cpu":
                    await asyncio.to_thread(self._set_model_device, self.preferred_device)
                scores = await asyncio.to_thread(self._engine.score_query_documents, query_documents)
            except Exception as error:
                error_text = str(error)
                can_retry = self._engine.supports_runtime_padding_retry(error_text)

                if can_retry and self._engine.force_batch_size_one():
                    print(
                        "[RERANKER] Batch/padding error detected at runtime. "
                        "Retrying rerank with batch_size=1."
                    )
                    try:
                        scores = await asyncio.to_thread(self._engine.score_query_documents, query_documents)
                    except Exception as retry_error:
                        print(
                            f"[RERANKER] Reranking failed after retry: {retry_error}. "
                            "Returning original order."
                        )
                        return [(doc, 0.0) for doc in documents[:top_k]]
                else:
                    print(f"[RERANKER] Reranking failed: {error}. Returning original order.")
                    return [(doc, 0.0) for doc in documents[:top_k]]
            finally:
                if self.swap_to_ram and self.preferred_device != "cpu":
                    try:
                        # Keep GPU memory free when reranker is idle.
                        await asyncio.to_thread(self._set_model_device, "cpu")
                        await asyncio.to_thread(gc.collect)
                        await asyncio.to_thread(clear_device_cache, self.preferred_device)
                    except Exception as offload_error:
                        print(f"[RERANKER] Failed to offload model to CPU: {offload_error}")

        doc_score_pairs = list(zip(documents, scores))
        doc_score_pairs.sort(key=lambda x: x[1], reverse=True)
        return doc_score_pairs[:top_k]
