import asyncio
import torch
from typing import List, Tuple
from sentence_transformers import CrossEncoder

class ZeRankerService:
    """
    Service for Reranking using BAAI/bge-reranker-v2-m3.
    This is a native BERT-based CrossEncoder that provides high accuracy
    without the prompt-formatting issues of LLM-based rerankers.
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        self.model_name = model_name
        
        # Detect device
        if torch.cuda.is_available():
            self.device = "cuda"
        elif torch.backends.mps.is_available():
            self.device = "mps"
        else:
            self.device = "cpu"
            
        print(f"⚖️  Loading Reranker Model: {model_name} on {self.device}...")

        try:
            # BGE-M3 is a standard CrossEncoder. 
            # It does NOT require trust_remote_code=True or padding hacks.
            self.model = CrossEncoder(
                model_name, 
                device=self.device,
                automodel_args={"torch_dtype": "auto"}
            )
            
            # Simple warmup to ensure model is loaded
            self.model.predict([("warmup", "check")])
            
            print(f"✅ Reranker ({model_name}) loaded successfully.")
            
        except Exception as e:
            print(f"❌ Failed to load Reranker: {e}")
            raise e

    async def rerank_documents(
        self, 
        query: str, 
        documents: List[str], 
        top_k: int = 5
    ) -> List[Tuple[str, float]]:
        """
        Reranks a list of document strings based on the query.
        """
        if not documents:
            return []

        # 1. Format pairs: [[query, doc1], [query, doc2], ...]
        query_documents = [[query, doc] for doc in documents]

        # 2. Run prediction (Blocking Code -> Async Thread)
        try:
            # model.predict returns a list of float scores
            scores = await asyncio.to_thread(self.model.predict, query_documents)
        except Exception as e:
            print(f"⚠️ Reranking failed: {e}. Returning original order.")
            return [(doc, 0.0) for doc in documents[:top_k]]

        # 3. Zip scores with documents and sort
        doc_score_pairs = list(zip(documents, scores))
        
        # Sort by score (High = Better)
        doc_score_pairs.sort(key=lambda x: x[1], reverse=True)

        return doc_score_pairs[:top_k]