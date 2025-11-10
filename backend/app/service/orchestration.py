"""
LangChain Orchestration Layer for RAG Pipeline
----------------------------------------------
This module implements the **Query-Orchestration layer** of the system.
It connects the FastAPI backend with:
- The Vector Database (AstraDB)
- The Beam-hosted Embedding & LLM Models

Responsibilities:
 Handles query refinement, vector retrieval, and context-aware response generation.

"""

from typing import List, Dict, Any, Optional
from langchain_core.prompts import PromptTemplate
from app.service.mock_beam_client import query_llm, get_embedding
from app.service.vector_store import VectorStoreService


class RAGOrchestrator:
    """
    Coordinates the Retrieval-Augmented Generation (RAG) pipeline
    for user queries.

    Steps:
    1. Refine the input query using LLM (optional)
    2. Retrieve relevant document chunks from AstraDB (vector search)
    3. Format retrieved text into a contextual prompt
    4. Generate a natural language answer with LLM
    """

    def __init__(self):
        """Initialize orchestrator with access to the vector store."""
        self.vector_store = VectorStoreService()

        # Template used to refine user queries
        self.query_refinement_prompt = PromptTemplate(
            input_variables=["original_query"],
            template=(
                "Given the user's question, extract the key search terms and concepts.\n"
                "Focus on the main topic and important keywords that would help retrieve relevant documents.\n\n"
                "Original Question: {original_query}\n\n"
                "Refined Search Query (concise, 5-10 words):"
            ),
        )

        # Template used to construct the final RAG prompt
        self.rag_prompt = PromptTemplate(
            input_variables=["context", "question"],
            template=(
                "You are a helpful AI assistant. Use the following context to answer the question.\n"
                "If the context doesn't contain relevant information, say so and provide a general response.\n\n"
                "Context:\n{context}\n\n"
                "Question: {question}\n\n"
                "Answer (be concise and accurate):"
            ),
        )

    # ------------------------
    # 1. Query Refinement
    # ------------------------
    def refine_query(self, user_query: str) -> str:
        """
        Use LLM to refine the user query for improved vector search performance.
        """
        try:
            prompt = self.query_refinement_prompt.format(original_query=user_query)
            refined = query_llm(
                prompt=prompt,
                max_new_tokens=50,
                temperature=0.3,  # low temp = deterministic
                top_p=0.9,
            )
            refined_query = refined.strip()

            # Fallback to original query if LLM failed
            if refined_query.startswith("[LLM") or refined_query.startswith("[Error]"):
                print(f"⚠️ Query refinement failed, using original: {user_query}")
                return user_query

            print(f"✅ Refined query: '{user_query}' → '{refined_query}'")
            return refined_query
        except Exception as e:
            print(f"⚠️ Query refinement error: {e}, using original query")
            return user_query

    # ------------------------
    # 2. Vector Retrieval
    # ------------------------
    def retrieve_relevant_chunks(
        self,
        query: str,
        user_id: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve top-k relevant document chunks from AstraDB
        using the query embedding.
        """
        try:
            query_embedding = get_embedding(query)
            if not query_embedding:
                print("⚠️ Failed to generate query embedding")
                return []

            results = self.vector_store.similarity_search(
                query_embedding=query_embedding,
                top_k=top_k,
                user_id=user_id,
            )
            print(f"✅ Retrieved {len(results)} relevant chunks")
            return results
        except Exception as e:
            print(f"❌ Retrieval error: {e}")
            return []

    # ------------------------
    # 3. Context Formatting
    # ------------------------
    def format_context(self, chunks: List[Dict[str, Any]]) -> str:
        """
        Combine multiple retrieved text chunks into a single
        context string for the LLM.
        """
        if not chunks:
            return "No relevant context found."

        context_parts = []
        for i, chunk in enumerate(chunks, 1):
            text = chunk.get("text", "")
            metadata = chunk.get("metadata", {})
            source = metadata.get("source", "Unknown")
            context_parts.append(f"[Source {i}: {source}]\n{text}")
        return "\n\n".join(context_parts)

    # ------------------------
    # 4. LLM Answer Generation
    # ------------------------
    def generate_response(
        self,
        question: str,
        context: str,
        max_tokens: int = 512,
    ) -> str:
        """Generate final answer using LLM with contextual prompt."""
        try:
            prompt = self.rag_prompt.format(context=context, question=question)
            response = query_llm(
                prompt=prompt,
                max_new_tokens=max_tokens,
                temperature=0.7,
                top_p=0.9,
            )
            return response
        except Exception as e:
            return f"[Error generating response] {str(e)}"

    # ------------------------
    # 5. Main RAG Workflow
    # ------------------------
    def process_query_with_rag(
        self,
        user_query: str,
        user_id: Optional[str] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """End-to-end RAG query pipeline."""
        print(f"\n{'='*60}")
        print(f"🔍 Processing query: {user_query}")
        print(f"{'='*60}\n")

        refined_query = self.refine_query(user_query)
        relevant_chunks = self.retrieve_relevant_chunks(
            query=refined_query,
            user_id=user_id,
            top_k=top_k,
        )
        context = self.format_context(relevant_chunks)
        answer = self.generate_response(question=user_query, context=context)

        sources = []
        for chunk in relevant_chunks:
            metadata = chunk.get("metadata", {})
            sources.append(
                {
                    "source": metadata.get("source", "Unknown"),
                    "similarity_score": chunk.get("similarity_score", 0.0),
                    "chunk_id": metadata.get("chunk_id", ""),
                }
            )

        print(f"\n✅ Query processing complete\n")

        return {
            "answer": answer,
            "sources": sources,
            "refined_query": refined_query,
            "num_sources": len(relevant_chunks),
            "has_context": len(relevant_chunks) > 0,
        }

    # ------------------------
    # 6. Fallback (No RAG)
    # ------------------------
    def process_query_without_rag(self, user_query: str) -> str:
        """
        Fallback: direct LLM response without retrieval.
        Triggered when user has no uploaded documents.
        """
        try:
            response = query_llm(
                prompt=user_query,
                max_new_tokens=512,
                temperature=0.7,
                top_p=0.9,
            )
            return response
        except Exception as e:
            return f"[Error] {str(e)}"


# ------------------------
# Singleton accessor
# ------------------------
_orchestrator = None


def get_orchestrator() -> RAGOrchestrator:
    """Get or create a single global RAG orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = RAGOrchestrator()
    return _orchestrator
