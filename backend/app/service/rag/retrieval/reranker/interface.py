from __future__ import annotations

"""
Common contract for all reranker backends.

The service layer (`ZeRankerService`) depends only on this interface so it can
switch between concrete implementations (BGE, Qwen, future models) without
changing orchestration logic.
"""

from abc import ABC, abstractmethod


class RerankerInterface(ABC):
    """Interface that every reranker backend must implement."""

    model_name: str
    backend_name: str

    @abstractmethod
    def set_device(self, target_device: str) -> None:
        """
        Move model weights to `target_device`.

        Called by the service for accelerator warm-path and swap-to-RAM flow.
        """

    @abstractmethod
    def score_query_documents(self, query_documents: list[list[str]]) -> list[float]:
        """
        Score each [query, document] pair and return one float per pair.

        Output order must match input order.
        """

    @abstractmethod
    def force_batch_size_one(self) -> bool:
        """
        Force backend to run with batch size 1.

        Returns:
            bool: True if a state change was applied, False if already size 1.
        """

    @abstractmethod
    def supports_runtime_padding_retry(self, error_text: str) -> bool:
        """
        Report whether an error can be recovered by shrinking batch size.

        Used by the service retry path after first inference failure.
        """
