# This is only used due to not wasting the credit of beam cloud embeddings
# For locally run only, need to be commented out when it is not used
# When using import this to vectordb_init.py, it will use local embedding model instead of beam cloud embedding service

import asyncio
from typing import List
from langchain_core.embeddings import Embeddings
from langchain_huggingface import HuggingFaceEmbeddings
import torch

class LocalGemmaEmbeddings(Embeddings):
    """
    Local embedding class using google/embeddinggemma-300m.
    Mirrors the structure of BeamGemmaEmbeddings (async + sync).
    """

    def __init__(self):
        super().__init__()

        print("🔧 Loading local embedding model: google/embeddinggemma-300m ...")

        if torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu" # Default to CPU if CUDA is not available

        self.embedding_model = HuggingFaceEmbeddings(
            model_name="google/embeddinggemma-300m",
            model_kwargs={"device": device},  # Changed from "cuda" to "cpu" for CPU-only environments
            encode_kwargs={"normalize_embeddings": True},
            show_progress=True
        )

        print(f"✅ Local Gemma Embedding Model Loaded Successfully!\nUsing device: {self.embedding_model.model_kwargs['device']}")

    # ==========================================================
    # INTERNAL ASYNC ENCODER
    # ==========================================================

    async def _aembed(self, texts: List[str]) -> List[List[float]]:
        """
        Async wrapper because HuggingFaceEmbeddings is synchronous.
        Runs the embedding in a thread to avoid blocking FastAPI.
        """
        loop = asyncio.get_event_loop()

        embeddings = await loop.run_in_executor(
            None,
            lambda: self.embedding_model.embed_documents(texts)
        )

        return embeddings

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        Async document embedding.
        """
        return await self._aembed(texts)

    async def aembed_query(self, text: str) -> List[float]:
        """
        Async query embedding.
        """
        result = await self._aembed([text])
        return result[0]

    # ==========================================================
    # SYNC FALLBACKS FOR LANGCHAIN
    # ==========================================================

    def _run_coro_safely(self, coro):
        """
        Allows sync embed_documents/embed_query to call async code,
        even when inside FastAPI's running event loop.
        """

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
        """
        Sync embedding (LangChain calls this method).
        """
        return self._run_coro_safely(self.aembed_documents(texts))

    def embed_query(self, text: str) -> List[float]:
        """
        Sync query embedding.
        """
        return self._run_coro_safely(self.aembed_query(text))
