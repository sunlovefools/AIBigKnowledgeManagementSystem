import os
import requests
from typing import List, Any
import aiohttp # 💡 Use asynchronous client for non-blocking I/O
import asyncio
from langchain_core.embeddings import Embeddings

# Configuration (Read from environment variables)
BEAM_ENDPOINT_URL = os.getenv("BEAM_EMBEDDING_URL")
BEAM_API_TOKEN = os.getenv("BEAM_EMBEDDINGS_KEY") 

class BeamGemmaEmbeddings(Embeddings):
    """
    Custom LangChain Embeddings class using synchronous 'requests'.
    
    This implementation resolves the startup conflict but uses blocking I/O for network calls.
    """
    
    def __init__(self, endpoint_url: str = BEAM_ENDPOINT_URL, api_token: str = BEAM_API_TOKEN, **kwargs: Any):
        """Initializes the Beam Embeddings client and authenticates."""
        super().__init__(**kwargs)
        
        if not endpoint_url or not api_token:
            raise ValueError(
                "Beam embedding URL (BEAM_EMBEDDING_URL) and API token (BEAM_EMBEDDINGS_KEY) must be provided."
            )
              
        self.endpoint_url = endpoint_url
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
    # ==========================================================
    # ASYNCHRONOUS IMPLEMENTATIONS (REQUIRED FOR NON-BLOCKING I/O)
    # ==========================================================
    
    async def _aembed(self, texts: List[str]) -> List[List[float]]:
        """Internal asynchronous worker using aiohttp."""
        payload = {"input": texts}
        
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.post(self.endpoint_url, json=payload, timeout=60) as response:
                    response.raise_for_status()
                    result = await response.json()
            
            # Note: Assuming the endpoint returns "embedding" or "embeddings" (using "embedding" for safety)
            embeddings = result.get("embeddings", [])
            
            if not isinstance(embeddings, list):
                raise ValueError("Beam endpoint did not return a valid list of embeddings.")
                
            return embeddings
            
        except aiohttp.ClientError as e:
            print(f"❌ Async Error calling Beam endpoint for batch of {len(texts)} texts: {e}")
            return [[]] * len(texts) 

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        """Async method called by VECTOR_STORE.aadd_documents()."""
        return await self._aembed(texts)

    async def aembed_query(self, text: str) -> List[float]:
        """Async method called by VECTOR_STORE.asimilarity_search()."""
        print("Embedding query asynchronously...")
        result = await self._aembed([text])
        return result[0] if result and result[0] else []

    # ==========================================================
    # SYNCHRONOUS FALLBACKS (MANDATORY FOR ABC/STARTUP)
    # ==========================================================

    def _run_coro_safely(self, coro):
        """Helper to run async code from sync context during startup."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return asyncio.run(coro)

        if loop.is_running():
            # Running inside FastAPI event loop → run coro in another thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                return executor.submit(asyncio.run, coro).result()

        else:
            # Loop exists but not running → run directly
            return loop.run_until_complete(coro)


    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Synchronous fallback method (calls async implementation)."""
        return self._run_coro_safely(self.aembed_documents(texts))


    def embed_query(self, text: str) -> List[float]:
        """Synchronous fallback method (calls async implementation)."""
        return self._run_coro_safely(self.aembed_query(text))