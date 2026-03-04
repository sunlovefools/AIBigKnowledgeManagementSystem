import asyncio
import os
from typing import List

import torch
from langchain_core.embeddings import Embeddings
from langchain_huggingface import HuggingFaceEmbeddings


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


class LocalGemmaEmbeddings(Embeddings):
    """
    Local embedding class using a Hugging Face model.

    Env:
    - LOCAL_EMBEDDING_MODEL: local embedding model name.
    - EMBEDDING_SWAP_TO_RAM: if true and accelerator exists, offload model to CPU RAM
      after each embedding call and move it back on-demand for next call.
    - EMBEDDING_GPU_INGEST_ONLY: if true, ingestion/document embeddings run on
      accelerator while query embeddings run on CPU RAM.
    """

    def __init__(
        self,
        model_name: str | None = None,
        *,
        swap_to_ram: bool | None = None,
        gpu_ingest_only: bool | None = None,
    ):
        super().__init__()

        resolved_model_name = (
            model_name
            or (os.getenv("LOCAL_EMBEDDING_MODEL") or "").strip()
            or "google/embeddinggemma-300m"
        )

        if swap_to_ram is None:
            swap_to_ram = _parse_bool_env("EMBEDDING_SWAP_TO_RAM", default=False)
        if gpu_ingest_only is None:
            gpu_ingest_only = _parse_bool_env("EMBEDDING_GPU_INGEST_ONLY", default=True)

        if torch.cuda.is_available():
            self.ingest_device = "cuda"
        elif torch.backends.mps.is_available():
            self.ingest_device = "mps"
        else:
            self.ingest_device = "cpu"

        # Query path should stay in CPU RAM when ingest-only GPU mode is enabled.
        if bool(gpu_ingest_only) and self.ingest_device != "cpu":
            self.query_device = "cpu"
        else:
            self.query_device = self.ingest_device

        self.swap_to_ram = bool(swap_to_ram)
        self.gpu_ingest_only = bool(gpu_ingest_only)
        self._encode_lock = asyncio.Lock()

        print(
            "Loading local embedding model: "
            f"{resolved_model_name} "
            f"(ingest_device={self.ingest_device}, query_device={self.query_device})..."
        )

        self.embedding_model = HuggingFaceEmbeddings(
            model_name=resolved_model_name,
            model_kwargs={"device": self.query_device},
            encode_kwargs={"normalize_embeddings": True},
            show_progress=True,
        )

        self.runtime_device = self.query_device
        self.model_name = resolved_model_name

        if self._should_swap_to_ram():
            print("EMBEDDING_SWAP_TO_RAM enabled. Offloading embedding model to CPU RAM.")
            self._move_model_to_device("cpu")

        print(
            f"Local embedding model loaded successfully. Active device: {self.runtime_device}"
        )

    def _should_swap_to_ram(self) -> bool:
        return self.swap_to_ram and self.ingest_device != "cpu"

    def _resolve_torch_model(self):
        # LangChain HuggingFaceEmbeddings keeps SentenceTransformer on `client`.
        candidates = [
            getattr(self.embedding_model, "client", None),
            getattr(self.embedding_model, "_client", None),
            getattr(self.embedding_model, "model", None),
        ]
        for candidate in candidates:
            if candidate is not None and hasattr(candidate, "to"):
                return candidate
        return None

    def _move_model_to_device(self, target_device: str) -> None:
        if self.runtime_device == target_device:
            return

        torch_model = self._resolve_torch_model()
        if torch_model is None:
            print(
                "Warning: unable to access underlying embedding model for device "
                "migration. Disabling EMBEDDING_SWAP_TO_RAM."
            )
            self.swap_to_ram = False
            return

        torch_target = torch.device(target_device)
        torch_model.to(torch_target)

        if isinstance(getattr(self.embedding_model, "model_kwargs", None), dict):
            self.embedding_model.model_kwargs["device"] = target_device

        self.runtime_device = target_device

        if target_device == "cpu":
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
                try:
                    torch.mps.empty_cache()
                except Exception:
                    pass

    def _embed_with_device_management(
        self,
        texts: List[str],
        *,
        active_device: str,
        keep_on_device_after_call: bool = False,
    ) -> List[List[float]]:
        if self.runtime_device != active_device:
            self._move_model_to_device(active_device)

        try:
            return self.embedding_model.embed_documents(texts)
        finally:
            should_offload_to_cpu = (
                self._should_swap_to_ram()
                or (self.gpu_ingest_only and active_device != self.query_device)
            )
            if should_offload_to_cpu and not keep_on_device_after_call:
                self._move_model_to_device("cpu")

    async def _aembed(self, texts: List[str]) -> List[List[float]]:
        """
        Async wrapper because HuggingFaceEmbeddings is synchronous.
        Runs embedding in a thread to avoid blocking FastAPI.
        """
        if not texts:
            return []

        loop = asyncio.get_event_loop()
        async with self._encode_lock:
            embeddings = await loop.run_in_executor(
                None,
                lambda: self._embed_with_device_management(
                    texts,
                    active_device=self.ingest_device,
                ),
            )
        return embeddings

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        return await self._aembed(texts)

    async def aembed_query(self, text: str) -> List[float]:
        if not text:
            return []

        loop = asyncio.get_event_loop()
        async with self._encode_lock:
            result = await loop.run_in_executor(
                None,
                lambda: self._embed_with_device_management(
                    [text],
                    active_device=self.query_device,
                ),
            )

        return result[0] if result else []

    def _run_coro_safely(self, coro):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return asyncio.run(coro)

        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                return executor.submit(asyncio.run, coro).result()

        return loop.run_until_complete(coro)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self._run_coro_safely(self.aembed_documents(texts))

    def embed_query(self, text: str) -> List[float]:
        return self._run_coro_safely(self.aembed_query(text))
