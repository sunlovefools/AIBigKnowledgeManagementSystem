import os
import json
from fastapi import FastAPI, HTTPException, Security, Depends, status
from dotenv import load_dotenv
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from contextlib import asynccontextmanager
import uvicorn
import engine
from typing import Any

load_dotenv()

security = HTTPBearer()

# Get token from environment variable
EXPECTED_API_KEY = os.getenv("API_TOKEN")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup and shutdown events.
    """
    engine.load_model()
    yield

app = FastAPI(lifespan=lifespan)

class AnswerRequest(BaseModel):
    """
    Request model for answer generation.
    """
    rag_context: str | list[dict[str, Any]]
    user_query: str

def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    """
    Validates the Bearer token.
    FastAPI automatically extracts the token from "Authorization: Bearer <token>"
    and puts it into credentials.credentials.
    """
    token = credentials.credentials
    if token != EXPECTED_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing Authentication Token",
        )
    return token

# --- Endpoint ---
@app.post("/generate_answer", dependencies=[Depends(verify_token)]) # Add dependency for token verification
async def generate_endpoint(request: AnswerRequest):
    try:
        if isinstance(request.rag_context, list):
            rag_context_text = json.dumps(request.rag_context, ensure_ascii=False, indent=2)
        else:
            rag_context_text = request.rag_context

        answer = engine.generate_answer(
            rag_context_text,
            request.user_query
        )
        return {"answer": answer}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    print("🚀 Starting Secured Service (Bearer Auth) on Port 8001")
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False)
