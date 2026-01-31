from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from contextlib import asynccontextmanager
import uvicorn
import engine

# Removed: dotenv, os, and fastapi.security imports since auth is no longer needed

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
    rag_context: str
    user_query: str

# Removed: verify_token function

# --- Endpoint ---
@app.post("/generate_answer") # Removed: dependencies=[Depends(verify_token)]
async def generate_endpoint(request: AnswerRequest):
    try:
        answer = engine.generate_answer(
            request.rag_context,
            request.user_query
        )
        return {"answer": answer}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    print("🚀 Starting Public Service (No Auth) on Port 8003")
    uvicorn.run("main:app", host="0.0.0.0", port=8003, reload=False)