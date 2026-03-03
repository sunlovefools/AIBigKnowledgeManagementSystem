import asyncio
import os
from typing import List, Tuple

import torch
from sentence_transformers import CrossEncoder


class ZeRankerService:
    """
    Service for reranking using BAAI/bge-reranker-v2-m3.

    Env:
    - RERANKER_SWAP_TO_RAM:
      If true and an accelerator is available, move the model to CPU RAM after
      each inference, and move it back to the accelerator before the next use.
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        self.model_name = model_name
        self.swap_to_ram_after_inference = self._parse_bool_env(
            "RERANKER_SWAP_TO_RAM", default=False
        )
        self._inference_lock = asyncio.Lock()

        # Detect runtime accelerator
        if torch.cuda.is_available():
            self.device = "cuda"
        elif torch.backends.mps.is_available():
            self.device = "mps"
        else:
            self.device = "cpu"

        print(f"Loading reranker model: {model_name} on {self.device}...")

        try:
            self.model = CrossEncoder(
                model_name,
                device=self.device,
                automodel_args={"torch_dtype": "auto"},
            )
            self.runtime_device = self.device

            # Warmup to ensure model is loaded once during startup.
            self.model.predict([("warmup", "check")])

            if self._should_swap_to_ram():
                print("RERANKER_SWAP_TO_RAM is enabled. Offloading reranker to CPU RAM.")
                self._move_model_to_device("cpu")

            print(f"Reranker ({model_name}) loaded successfully.")
        except Exception as error:
            print(f"Failed to load reranker: {error}")
            raise

    @staticmethod
    def _parse_bool_env(name: str, *, default: bool) -> bool:
        """Parse a boolean environment variable with common truthy/falsy values."""
        raw = os.getenv(name)
        if raw is None:
            return default

        value = raw.strip().lower()
        if value in {"1", "true", "yes", "y", "on"}:
            return True
        if value in {"0", "false", "no", "n", "off"}:
            return False
        return default

    def _should_swap_to_ram(self) -> bool:
        return self.swap_to_ram_after_inference and self.device != "cpu"

    def _move_model_to_device(self, target_device: str) -> None:
        if getattr(self, "runtime_device", None) == target_device:
            return

        torch_target = torch.device(target_device)

        # CrossEncoder wraps an HF sequence classification model under `.model`.
        if hasattr(self.model, "model"):
            self.model.model.to(torch_target)
        elif hasattr(self.model, "to"):
            self.model.to(torch_target)
        else:
            raise RuntimeError("Unable to move reranker model to a different device.")

        # CrossEncoder may expose `_target_device` for predict routing.
        # Do not assign `device` directly: in newer versions it's read-only.
        if hasattr(self.model, "_target_device"):
            self.model._target_device = torch_target

        self.runtime_device = target_device

        # Release accelerator memory once offloaded.
        if target_device == "cpu":
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
                try:
                    torch.mps.empty_cache()
                except Exception:
                    pass

    async def rerank_documents(
        self,
        query: str,
        documents: List[str],
        top_k: int = 5,
    ) -> List[Tuple[str, float]]:
        """
        Rerank a list of document strings based on the query.
        """
        if not documents:
            return []

        try:
            top_k = int(top_k)
        except (TypeError, ValueError):
            top_k = 5

        if top_k <= 0:
            return []

        top_k = min(top_k, len(documents))
        query_documents = [[query, doc] for doc in documents]

        async with self._inference_lock:
            try:
                # Move back to accelerator if currently offloaded to CPU RAM.
                if getattr(self, "runtime_device", self.device) != self.device:
                    print(f"Moving reranker model from RAM to {self.device} for inference.")
                    await asyncio.to_thread(self._move_model_to_device, self.device)

                raw_scores = await asyncio.to_thread(self.model.predict, query_documents)
                scores = list(raw_scores)

                if len(scores) != len(documents):
                    raise RuntimeError(
                        f"Reranker returned {len(scores)} scores for {len(documents)} documents."
                    )
            except Exception as error:
                print(f"Reranking failed: {error}. Returning original order.")
                return [(doc, 0.0) for doc in documents[:top_k]]
            finally:
                if self._should_swap_to_ram():
                    try:
                        await asyncio.to_thread(self._move_model_to_device, "cpu")
                    except Exception as offload_error:
                        print(f"Failed to offload reranker to CPU RAM: {offload_error}")

        doc_score_pairs = list(zip(documents, scores))
        doc_score_pairs.sort(key=lambda item: item[1], reverse=True)
        return doc_score_pairs[:top_k]
