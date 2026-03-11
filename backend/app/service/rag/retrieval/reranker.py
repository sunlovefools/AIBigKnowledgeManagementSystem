import asyncio
import gc
import os
from typing import Any, List, Tuple

import torch
from sentence_transformers import CrossEncoder

DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

SUPPORTED_RERANKER_MODELS: dict[str, dict[str, Any]] = {
    "BAAI/bge-reranker-v2-m3": {
        "model_kwargs": {"torch_dtype": "auto"},
    },
    "Qwen/Qwen3-Reranker-0.6B": {
        "model_kwargs": {"torch_dtype": "auto"},
        "trust_remote_code": True,
    },
}


def _parse_bool_env(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _detect_preferred_device() -> str:
    if torch.cuda.is_available():
        return "cuda"

    mps_backend = getattr(torch.backends, "mps", None)
    if mps_backend is not None and mps_backend.is_available():
        return "mps"

    return "cpu"


def _clear_device_cache(device: str) -> None:
    if device == "cuda" and torch.cuda.is_available():
        torch.cuda.empty_cache()
        return

    if device == "mps":
        mps_module = getattr(torch, "mps", None)
        if mps_module is not None and hasattr(mps_module, "empty_cache"):
            mps_module.empty_cache()


class ZeRankerService:
    """
    Sentence-transformers CrossEncoder wrapper with env-based model selection
    and optional accelerator offload when idle.
    """

    def __init__(self, model_name: str | None = None):
        configured_model = model_name or os.getenv("RERANKER_MODEL", DEFAULT_RERANKER_MODEL)
        if configured_model not in SUPPORTED_RERANKER_MODELS:
            print(
                f"[RERANKER] Unsupported RERANKER_MODEL={configured_model!r}. "
                f"Falling back to {DEFAULT_RERANKER_MODEL!r}."
            )
            configured_model = DEFAULT_RERANKER_MODEL

        self.model_name = configured_model
        self.swap_to_ram = _parse_bool_env(os.getenv("RERANKER_SWAP_TO_RAM"), default=False)
        self.preferred_device = _detect_preferred_device()
        # If swap is enabled, keep idle model on CPU and move to accelerator only for inference.
        self.active_device = (
            "cpu" if self.swap_to_ram and self.preferred_device != "cpu" else self.preferred_device
        )
        self._inference_lock = asyncio.Lock()
        self._predict_kwargs: dict[str, Any] = {}

        print(
            f"[RERANKER] Loading model={self.model_name}, "
            f"preferred_device={self.preferred_device}, initial_device={self.active_device}, "
            f"swap_to_ram={self.swap_to_ram}"
        )

        try:
            model_kwargs = dict(SUPPORTED_RERANKER_MODELS[self.model_name])
            self.model = CrossEncoder(self.model_name, device=self.active_device, **model_kwargs)
            self._ensure_padding_token()

            # Warmup to validate model init and tokenizer path.
            self.model.predict([("warmup", "check")])
            print(f"[RERANKER] Model {self.model_name!r} loaded successfully.")
        except Exception as error:
            print(f"[RERANKER] Failed to load model {self.model_name!r}: {error}")
            raise error

    def _set_cross_encoder_device(self, target_device: str) -> None:
        if target_device == self.active_device:
            return

        # CrossEncoder wraps AutoModelForSequenceClassification in .model.
        self.model.model.to(target_device)
        self.model._target_device = torch.device(target_device)
        self.active_device = target_device

    def _ensure_padding_token(self) -> None:
        tokenizer = getattr(self.model, "tokenizer", None)
        if tokenizer is None:
            return

        pad_token_id = getattr(tokenizer, "pad_token_id", None)
        if pad_token_id is None:
            for base_name in ("eos", "sep", "unk"):
                token = getattr(tokenizer, f"{base_name}_token", None)
                token_id = getattr(tokenizer, f"{base_name}_token_id", None)
                if token is None or token_id is None:
                    continue

                try:
                    tokenizer.pad_token = token
                except Exception:
                    continue

                try:
                    tokenizer.pad_token_id = token_id
                except Exception:
                    pass
                break

        pad_token_id = getattr(tokenizer, "pad_token_id", None)
        if pad_token_id is None:
            self._predict_kwargs["batch_size"] = 1
            print(
                "[RERANKER] No padding token available; forcing batch_size=1 "
                "for reranking."
            )
            return

        model_config = getattr(self.model.model, "config", None)
        if model_config is not None and getattr(model_config, "pad_token_id", None) is None:
            model_config.pad_token_id = pad_token_id

    async def rerank_documents(
        self,
        query: str,
        documents: List[str],
        top_k: int = 5,
    ) -> List[Tuple[str, float]]:
        """
        Reranks a list of document strings based on the query.
        """
        if not documents:
            return []

        query_documents = [[query, doc] for doc in documents]

        async with self._inference_lock:
            try:
                if self.preferred_device != "cpu":
                    await asyncio.to_thread(self._set_cross_encoder_device, self.preferred_device)

                # model.predict is blocking, run in worker thread.
                scores = await asyncio.to_thread(self.model.predict, query_documents, **self._predict_kwargs)
            except Exception as error:
                error_text = str(error)
                missing_padding_msg = "Cannot handle batch sizes > 1 if no padding token is defined"

                if missing_padding_msg in error_text and self._predict_kwargs.get("batch_size") != 1:
                    print(
                        "[RERANKER] Batch/padding error detected at runtime. "
                        "Retrying rerank with batch_size=1."
                    )
                    self._predict_kwargs["batch_size"] = 1
                    try:
                        scores = await asyncio.to_thread(
                            self.model.predict,
                            query_documents,
                            **self._predict_kwargs,
                        )
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
                        await asyncio.to_thread(self._set_cross_encoder_device, "cpu")
                        await asyncio.to_thread(gc.collect)
                        await asyncio.to_thread(_clear_device_cache, self.preferred_device)
                    except Exception as offload_error:
                        print(f"[RERANKER] Failed to offload model to CPU: {offload_error}")

        doc_score_pairs = list(zip(documents, scores))
        doc_score_pairs.sort(key=lambda x: x[1], reverse=True)
        return doc_score_pairs[:top_k]
