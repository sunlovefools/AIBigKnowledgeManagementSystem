import os
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Security, Depends, status
from dotenv import load_dotenv
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from contextlib import asynccontextmanager
import uvicorn
import engine

load_dotenv()

security = HTTPBearer()
EXPECTED_API_KEY = os.getenv("API_TOKEN")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup and shutdown events.
    """
    engine.load_model()
    yield


app = FastAPI(lifespan=lifespan)


# -------------------------
# Request Models
# -------------------------

class RetrievedChunk(BaseModel):
    filename: str
    chunk_context: str
    page: Optional[int] = None


class AnswerRequest(BaseModel):
    # Old format (backward compatible)
    rag_context: Optional[str] = None

    # New structured format
    retrieved_contexts: Optional[List[RetrievedChunk]] = None

    # Required
    user_query: str


# -------------------------
# Authentication
# -------------------------

def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    token = credentials.credentials
    if token != EXPECTED_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing Authentication Token",
        )
    return token


# -------------------------
# Endpoint
# -------------------------

@app.post("/generate_answer", dependencies=[Depends(verify_token)])
async def generate_endpoint(request: AnswerRequest):
    try:
        # Structured format
        if request.retrieved_contexts:
            answer = engine.generate_answer(
                request.retrieved_contexts,
                request.user_query
            )

        # Old format
        elif request.rag_context:
            answer = engine.generate_answer(
                request.rag_context,
                request.user_query
            )

        else:
            raise HTTPException(
                status_code=400,
                detail="No context provided. Provide either 'rag_context' or 'retrieved_contexts'."
            )

        return {"answer": answer}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    print("🚀 Starting Secured Service (Bearer Auth) on Port 8001")
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False)
