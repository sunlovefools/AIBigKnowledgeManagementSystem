from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.service.query_refiner import refine_query
from app.service.vectordb import search_and_retrieve_context
from app.service.answer_generator import generate_answer

# Setup the API router
router = APIRouter()


# --- Request/Response Models ---
class QueryRequest(BaseModel):
    query: str
    top_k: int = 5  # Number of similar child documents to retrieve

class QueryResponse(BaseModel):
    answer: str


# --- Health Check ---
@router.get("/health")
def query_health():
    return {"query_service": "ok"}


# --- Main RAG Query Endpoint (with refinement) ---
@router.post("/query", response_model=QueryResponse)
async def query_documents(request: QueryRequest):
    """
    Full RAG Query Pipeline using the Parent-Child Retriever pattern:
    1. Refine the user query using LLM.
    2. Search for relevant child chunks and retrieve associated parent document contents (LangChain/AstraDB).
    3. Generate an answer from the context using the Answer Generator LLM.
    """

    # --- Step 1: Query Refinement using LLM ---
    print(f"📝 Original Query: {request.query}")

    try:
        # Get the LLM to refine the query
        refined_query = await refine_query(request.query)
        print(f"✨ Refined Query: {refined_query}")
    except Exception as error:
        print(f"❌ Query refinement failed: {error}")
        raise HTTPException(
            status_code=500,
            detail=f"Query refinement failed: {str(error)}"
        )

    # --- Step 2: Retrieval of Parent Documents (Full Context) ---
    try:
        # search_and_retrieve_context performs vector search on child chunks 
        # and looks up the full content from the parent documents.
        rag_contents = await search_and_retrieve_context(
            query=refined_query,
            top_k=request.top_k
        )

        if not rag_contents:
            return QueryResponse(answer="No relevant documents found for your query. Try ingesting more data.")

        print(f"🔍 Retrieved context from {len(rag_contents)} parent documents.")

    except Exception as e:
        print(f"❌ Retrieval failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Context retrieval failed: {str(e)}"
        )


    # ---- Step 3: Send to Beam LLM Answer Generator ----
    try:
        answer = await generate_answer(rag_contents, request.query)
        print("🧠 Beam Answer Generated!")
    except Exception as error:
        print(f"❌ Beam Answer Generator Failed: {error}")
        raise HTTPException(
            status_code=500,
            detail=f"Beam answer generation failed: {str(error)}"
        )

    print("✅ Query Process Completed Successfully")
    return QueryResponse(
        answer=answer
    )


# # --- Alternative Endpoint: Query without Refinement ---
# @router.post("/query/direct", response_model=QueryResponse)
# async def query_documents_direct(request: QueryRequest):
#     """
#     Direct query without LLM refinement (Parent-Child Retriever).
#     This uses the original user query for embedding and retrieval.
#     """

#     print(f"📝 Direct Query (No Refinement): {request.query}")
    
#     # Use original query text for retrieval
#     query_text = request.query

#     # --- Step 1: Retrieval of Parent Documents (Full Context) ---
#     try:
#         rag_contents = await search_and_retrieve_context(
#             query=query_text,
#             top_k=request.top_k
#         )

#         if not rag_contents:
#             return QueryResponse(answer="No relevant documents found for your query. Try ingesting more data.")
        
#         print(f"🔍 Retrieved context from {len(rag_contents)} parent documents.")

#     except Exception as e:
#         print(f"❌ Retrieval failed: {e}")
#         raise HTTPException(
#             status_code=500,
#             detail=f"Context retrieval failed: {str(e)}"
#         )

#     # ---- Step 2: Send to Beam LLM Answer Generator ----
#     try:
#         answer = await generate_answer(rag_contents, request.query)
#         print("🧠 Beam Answer Generated!")
#     except Exception as e:
#         print(f"❌ Beam Answer Generator Failed: {e}")
#         raise HTTPException(
#             status_code=500,
#             detail=f"Beam answer generation failed: {str(e)}"
#         )
        
#     print("✅ Direct Query Process Completed Successfully")
#     return QueryResponse(
#         answer=answer
#     )