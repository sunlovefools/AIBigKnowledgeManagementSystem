from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import aiohttp
from typing import List, Dict, Any

from app.service.query_refiner import refine_query
from app.service.embedder import embed_text
from app.service.vector_store import search_similar_chunks

# Setup the API router
router = APIRouter()


# --- Request/Response Models ---
class QueryRequest(BaseModel):
    query: str
    top_k: int = 5  # Number of similar documents to retrieve


class RetrievedChunk(BaseModel):
    content: str
    document_name: str
    page_number: int
    chunk_number: int
    similarity_score: float
    uploaded_by: str
    timestamp: str


class QueryResponse(BaseModel):
    original_query: str
    refined_query: str
    retrieved_chunks: List[RetrievedChunk]
    chunks_count: int


# --- Health Check ---
@router.get("/health")
def query_health():
    return {"query_service": "ok"}


# --- Main RAG Query Endpoint ---
@router.post("/query", response_model=QueryResponse)
async def query_documents(request: QueryRequest):
    """
    RAG Query Pipeline:
    1. Refine the user query using LLM
    2. Generate embeddings for the refined query
    3. Perform similarity search in vector database
    4. Return top K most similar chunks
    
    Args:
        request: QueryRequest containing the user's query and top_k parameter
        
    Returns:
        QueryResponse with refined query and retrieved document chunks
    """
    
    # --- Step 1: Query Refinement using LLM ---
    print(f"📝 Original Query: {request.query}")
    
    try:
        refined_query = await refine_query(request.query)
        print(f"✨ Refined Query: {refined_query}")
    except Exception as e:
        print(f"❌ Query refinement failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Query refinement failed: {str(e)}"
        )
    
    # --- Step 2: Generate Embedding for Refined Query ---
    try:
        # Prepare payload for embeddings (same format as document ingestion)
        query_payload = {
            "input": [refined_query]
        }
        
        async with aiohttp.ClientSession() as session:
            embedding_response = await embed_text(session, query_payload)
            print(f"🔢 Embedding Response: {embedding_response}")
            
        # Extract the embedding vector
        embeddings = embedding_response.get("embedding")
        
        if not embeddings or len(embeddings) == 0:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to generate query embedding. Got: {embedding_response}"
            )
        
        query_embedding = embeddings[0]  # Get the first (and only) embedding
        print(f"✅ Query embedding generated (dimension: {len(query_embedding)})")
        
    except Exception as e:
        print(f"❌ Embedding generation failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Embedding generation failed: {str(e)}"
        )
    
    # --- Step 3: Similarity Search in Vector Database ---
    try:
        similar_chunks = search_similar_chunks(
            query_embedding=query_embedding,
            top_k=request.top_k
        )
        print(f"🔍 Found {len(similar_chunks)} similar chunks")
        
    except Exception as e:
        print(f"❌ Similarity search failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Similarity search failed: {str(e)}"
        )
    
    # --- Step 4: Format Response ---
    # Format response
    retrieved_chunks = [
    RetrievedChunk(
        content=chunk["content"],
        document_name=chunk["document_name"],
        page_number=chunk["page_number"],
        chunk_number=chunk["chunk_number"],
        similarity_score=float(chunk.get("similarity_score") or 0.0),
        uploaded_by=chunk["uploaded_by"],
        timestamp=chunk["timestamp"]
    )
    for chunk in similar_chunks
    ]
    
    return QueryResponse(
        original_query=request.query,
        refined_query=refined_query,
        retrieved_chunks=retrieved_chunks,
        chunks_count=len(retrieved_chunks)
    )


# --- Alternative Endpoint: Query without Refinement ---
@router.post("/query/direct", response_model=QueryResponse)
async def query_documents_direct(request: QueryRequest):
    """
    Direct query without LLM refinement.
    Useful for debugging or when refinement is not needed.
    """
    
    print(f"📝 Direct Query (No Refinement): {request.query}")
    
    # Skip refinement, use original query
    refined_query = request.query
    
    # Generate embedding
    try:
        query_payload = {"input": [refined_query]}
        
        async with aiohttp.ClientSession() as session:
            embedding_response = await embed_text(session, query_payload)
            
        embeddings = embedding_response.get("embedding")
        
        if not embeddings or len(embeddings) == 0:
            raise HTTPException(
                status_code=500,
                detail="Failed to generate query embedding"
            )
        
        query_embedding = embeddings[0]
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Embedding generation failed: {str(e)}"
        )
    
    # Similarity search
    try:
        similar_chunks = search_similar_chunks(
            query_embedding=query_embedding,
            top_k=request.top_k
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Similarity search failed: {str(e)}"
        )
    
    # Format response
    retrieved_chunks = [
    RetrievedChunk(
        content=chunk["content"],
        document_name=chunk["document_name"],
        page_number=chunk["page_number"],
        chunk_number=chunk["chunk_number"],
        similarity_score=float(chunk.get("similarity_score") or 0.0),
        uploaded_by=chunk["uploaded_by"],
        timestamp=chunk["timestamp"]
    )
    for chunk in similar_chunks
    ]

    
    return QueryResponse(
        original_query=request.query,
        refined_query=refined_query,
        retrieved_chunks=retrieved_chunks,
        chunks_count=len(retrieved_chunks)
    )