from __future__ import annotations

"""BGE reranker backend implementation using sentence-transformers CrossEncoder."""

from typing import Any

import torch
from sentence_transformers import CrossEncoder

from .interface import RerankerInterface
from .utils import ensure_tokenizer_padding


class BGEReranker(RerankerInterface):
    """Concrete reranker backend for `BAAI/bge-reranker-v2-m3`."""

    backend_name = "cross_encoder"
    _MISSING_PADDING_MSG = "Cannot handle batch sizes > 1 if no padding token is defined"

    def __init__(self, model_name: str, *, device: str, model_kwargs: dict[str, Any] | None = None):
        """Load CrossEncoder model and run a warmup inference."""
        self.model_name = model_name
        self._predict_kwargs: dict[str, Any] = {}
        self.model = CrossEncoder(
            self.model_name,
            device=device,
            **(model_kwargs or {}),
        )

        tokenizer = getattr(self.model, "tokenizer", None)
        model_config = getattr(getattr(self.model, "model", None), "config", None)
        has_padding = ensure_tokenizer_padding(tokenizer=tokenizer, model_config=model_config)
        if not has_padding:
            self._predict_kwargs["batch_size"] = 1
            print("[RERANKER] No padding token for cross-encoder; forcing batch_size=1.")

        self.model.predict([("warmup", "check")], **self._predict_kwargs)

    def set_device(self, target_device: str) -> None:
        """Move CrossEncoder model to target device."""
        self.model.model.to(target_device)
        self.model._target_device = torch.device(target_device)

    def score_query_documents(self, query_documents: list[list[str]]) -> list[float]:
        """Score query-document pairs with CrossEncoder predict."""
        return self.model.predict(query_documents, **self._predict_kwargs)

    def force_batch_size_one(self) -> bool:
        """Switch to single-item batches for runtime padding recovery."""
        if self._predict_kwargs.get("batch_size") == 1:
            return False
        self._predict_kwargs["batch_size"] = 1
        return True

    def supports_runtime_padding_retry(self, error_text: str) -> bool:
        """Detect CrossEncoder's known missing-padding runtime error."""
        return self._MISSING_PADDING_MSG in error_text
