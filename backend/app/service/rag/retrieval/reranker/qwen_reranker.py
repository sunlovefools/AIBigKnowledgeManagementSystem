from __future__ import annotations

"""
Qwen3 reranker backend implementation.

This backend follows instruction-aware prompt construction and computes
relevance as `(logit_yes - logit_no)` from final-token logits.
"""

from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from .interface import RerankerInterface
from .utils import ensure_tokenizer_padding, to_pylist

QWEN_RERANK_INSTRUCTION = (
    "Rank documents by how well they answer the query. "
    "Prioritize semantic relevance. "
    "Boost documents with important exact keywords (entities, acronyms, technical terms). "
    "Do not favor documents that only repeat keywords without useful content."
)


class QwenReranker(RerankerInterface):
    """Concrete reranker backend for `Qwen/Qwen3-Reranker-0.6B`."""

    backend_name = "qwen_causal_lm"
    _MISSING_PADDING_MSG = "Cannot handle batch sizes > 1 if no padding token is defined"

    def __init__(
        self,
        model_name: str,
        *,
        device: str,
        model_kwargs: dict[str, Any] | None = None,
        trust_remote_code: bool = True,
    ):
        """Load tokenizer/model, normalize padding, and warm up once."""
        self.model_name = model_name
        self._batch_size = 32

        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_name,
            trust_remote_code=trust_remote_code,
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            trust_remote_code=trust_remote_code,
            **(model_kwargs or {}),
        )
        self.model.eval()
        self.model.to(device)

        has_padding = ensure_tokenizer_padding(tokenizer=self.tokenizer, model_config=self.model.config)
        if not has_padding:
            self._batch_size = 1
            print("[RERANKER] No padding token for Qwen; forcing batch_size=1.")

        self._yes_token_id = self._single_token_id("yes")
        self._no_token_id = self._single_token_id("no")

        self.score_query_documents([["warmup", "check"]])

    def _single_token_id(self, text: str) -> int:
        """Resolve one representative token id for target label text."""
        token_ids = self.tokenizer.encode(text, add_special_tokens=False)
        if not token_ids:
            raise RuntimeError(f"Tokenizer returned no token IDs for {text!r}.")

        if len(token_ids) > 1:
            print(
                f"[RERANKER] Token {text!r} maps to multiple IDs {token_ids}; "
                f"using last ID {token_ids[-1]}."
            )

        return int(token_ids[-1])

    def _build_prompt(self, query: str, document: str) -> str:
        """Build the model-specific instruction prompt for one candidate doc."""
        return (
            f"<Instruct>: {QWEN_RERANK_INSTRUCTION}\n"
            f"<Query>: {query}\n"
            f"<Document>: {document}"
        )

    def set_device(self, target_device: str) -> None:
        """Move causal-LM weights to target device."""
        self.model.to(target_device)

    def score_query_documents(self, query_documents: list[list[str]]) -> list[float]:
        """
        Score query-document pairs with Qwen final-token yes/no logits.

        Processing is batched with dynamic fallback to batch_size=1 when needed.
        """
        prompts = [self._build_prompt(query=q, document=d) for q, d in query_documents]

        all_scores: list[float] = []
        step = max(int(self._batch_size), 1)
        for start in range(0, len(prompts), step):
            prompt_batch = prompts[start : start + step]
            encoded = self.tokenizer(
                prompt_batch,
                padding=True,
                truncation=True,
                return_tensors="pt",
            )
            model_inputs = {
                key: (value.to(self.model.device) if hasattr(value, "to") else value)
                for key, value in encoded.items()
            }

            no_grad_ctx = getattr(torch, "no_grad", None)
            if callable(no_grad_ctx):
                with no_grad_ctx():
                    outputs = self.model(**model_inputs)
            else:
                outputs = self.model(**model_inputs)

            logits = to_pylist(outputs.logits)
            attention_mask = to_pylist(model_inputs.get("attention_mask"))

            for idx, sample_logits in enumerate(logits):
                if not sample_logits:
                    all_scores.append(0.0)
                    continue

                if attention_mask is not None and idx < len(attention_mask):
                    last_index = max(int(sum(attention_mask[idx])) - 1, 0)
                else:
                    last_index = len(sample_logits) - 1

                if last_index >= len(sample_logits):
                    last_index = len(sample_logits) - 1

                # Official Qwen reranker-style relevance: yes-logit minus no-logit.
                last_token_logits = sample_logits[last_index]
                yes_logit = float(last_token_logits[self._yes_token_id])
                no_logit = float(last_token_logits[self._no_token_id])
                all_scores.append(yes_logit - no_logit)

        return all_scores

    def force_batch_size_one(self) -> bool:
        """Switch Qwen scoring to single-item batches for retry path."""
        if self._batch_size == 1:
            return False
        self._batch_size = 1
        return True

    def supports_runtime_padding_retry(self, error_text: str) -> bool:
        """Detect runtime padding errors recoverable by batch-size reduction."""
        return self._MISSING_PADDING_MSG in error_text
