from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from datetime import datetime
from app.service.query_service import process_query

router = APIRouter()

# ----- Request & Response Schemas -----

class QueryRequest(BaseModel):
    """
    The request body sent from the frontend.
    """
    user_id: str
    query: str


class QueryResponse(BaseModel):
    """
    The structured response returned to the frontend.
    """
    answer: str
    timestamp: datetime


# ----- API Endpoint -----

@router.post("/query", response_model=QueryResponse, tags=["Query"])
async def handle_query(request: QueryRequest):
    """
    Handle user query requests.
    - Receives a user prompt and ID from the frontend.
    - Calls the query service for processing.
    - Returns the answer (mock for now).
    """
    try:
        result = process_query(user_id=request.user_id, query=request.query)
        return QueryResponse(answer=result["answer"], timestamp=datetime.utcnow())
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query failed: {str(e)}"
        )
