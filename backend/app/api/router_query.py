from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Local imports
from app.service.rag.retrieval.query_refiner import refine_query
from app.vectordb.vectordb import search_and_retrieve_context
from app.service.rag.retrieval.answer_generator import generate_answer

# Setup the API router
router = APIRouter()

# --- Data Models ---
class QueryRequest(BaseModel):
    """
    Request model for RAG query endpoint.
    """
    query: str
    top_k: int = 10  # Number of similar child documents to retrieve

class QueryResponse(BaseModel):
    """
    Response model for RAG query endpoint.
    """
    answer: str


# --- query/ endpoint ---

@router.get("/health")
def query_health():
    """
    Health check endpoint for query endpoint.
    """
    return {"query_service": "ok"}

@router.post("/query", response_model=QueryResponse)
async def query_documents(request: QueryRequest):
    """
    Full RAG Query Pipeline using the Parent-Child Retriever pattern:
    1. Refine the user query using LLM. (It is currently skipped as experiment)
    2. Search for relevant child chunks and retrieve associated parent document contents (LangChain/AstraDB).
    3. Generate an answer from the context using the Answer Generator LLM.
    """

    # --- Step 1: Retrieval of Parent Documents (Full Context) ---
    try:
        # search_and_retrieve_context performs vector search on child chunks 
        # and looks up the full content from the parent documents.
        rag_docs = await search_and_retrieve_context(
            query=request.query,
            top_k=request.top_k
        )

        if not rag_docs:
            return QueryResponse(answer="No relevant documents found for your query. Try ingesting more data.")

    except Exception as e:
        print(f"❌ Retrieval failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Context retrieval failed: {str(e)}"
        )


    # ---- Step 2: Send to LLM for Answer Generation ----
    try:
        answer = await generate_answer(rag_docs, request.query)
        print("🧠 LLM Answer Generated!")
    except Exception as error:
        print(f"❌ LLM Answer Generation Failed: {error}")
        raise HTTPException(
            status_code=500,
            detail=f"LLM answer generation failed: {str(error)}"
        )

    print("✅ Query Process Completed Successfully")
    return QueryResponse(
        answer=answer
    )
