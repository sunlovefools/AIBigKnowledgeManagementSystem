"""
Query Service
RAG
"""

from typing import Dict, Any
from app.service.orchestration import get_orchestrator


def process_query(user_id: str, query: str, use_rag: bool = True) -> Dict[str, Any]:
    """
    Process the user query using RAG pipeline or direct LLM.
    
    Args:
        user_id: User identifier for personalized retrieval
        query: User's question
        use_rag: Whether to use RAG (retrieval) or direct LLM
        
    Returns:
        Dictionary containing:
        - answer: The generated response
        - sources: List of source documents used (if RAG)
        - metadata: Additional processing information
    """
    try:
        # Get the orchestrator instance
        orchestrator = get_orchestrator()
        
        if use_rag:
            # Process query with full RAG pipeline
            result = orchestrator.process_query_with_rag(
                user_query=query,
                user_id=user_id,
                top_k=5  # Retrieve top 5 most similar chunks
            )
            
            # Return comprehensive result
            return {
                "answer": result['answer'],
                "sources": result.get('sources', []),
                "refined_query": result.get('refined_query', query),
                "num_sources": result.get('num_sources', 0),
                "has_context": result.get('has_context', False),
                "mode": "RAG"
            }
        else:
            # Direct LLM query without retrieval
            llm_answer = orchestrator.process_query_without_rag(query)
            
            return {
                "answer": llm_answer,
                "sources": [],
                "num_sources": 0,
                "has_context": False,
                "mode": "Direct LLM"
            }

    except Exception as e:
        # Fallback error handling
        print(f"❌ Query processing error: {e}")
        return {
            "answer": f"[Error processing query] {str(e)}",
            "sources": [],
            "num_sources": 0,
            "has_context": False,
            "mode": "Error"
        }


def check_user_has_documents(user_id: str) -> bool:
    """
    Check if user has uploaded any documents.
    
    Args:
        user_id: User identifier
        
    Returns:
        True if user has documents, False otherwise
    """
    try:
        from app.service.vector_store import get_vector_store
        vector_store = get_vector_store()
        count = vector_store.get_user_document_count(user_id)
        return count > 0
    except Exception as e:
        print(f"⚠️  Failed to check user documents: {e}")
        return False


def get_user_sources(user_id: str) -> Dict[str, Any]:
    """
    Get list of all document sources uploaded by user.
    
    Args:
        user_id: User identifier
        
    Returns:
        Dictionary with source list and count
    """
    try:
        from app.service.vector_store import get_vector_store
        vector_store = get_vector_store()
        
        sources = vector_store.list_user_sources(user_id)
        count = vector_store.get_user_document_count(user_id)
        
        return {
            "sources": sources,
            "total_sources": len(sources),
            "total_chunks": count,
            "success": True
        }
    except Exception as e:
        print(f"❌ Failed to get user sources: {e}")
        return {
            "sources": [],
            "total_sources": 0,
            "total_chunks": 0,
            "success": False,
            "error": str(e)
        }