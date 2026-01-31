import os
import time
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Security, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from contextlib import asynccontextmanager
from typing import List, Optional

load_dotenv()

# Import local engine
import engine

# Security Setup (Same as your Answer Generator)
security = HTTPBearer()
EXPECTED_API_KEY = os.getenv("JUDGE_API_TOKEN")

@asynccontextmanager
async def lifespan(app: FastAPI):
    engine.load_model()
    yield

app = FastAPI(lifespan=lifespan, title="Ragas Judge Service")

# --- Pydantic Models for OpenAI Compatibility ---
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str = "default"
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.0

# --- Auth Helper ---
def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    token = credentials.credentials
    if token != EXPECTED_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Authentication Token",
        )
    return token

# --- The Endpoint ---
# Ragas (via LangChain ChatOpenAI) sends POST requests to /chat/completions
# We add /v1 to match standard OpenAI paths.
@app.post("/v1/chat/completions", dependencies=[Depends(verify_token)])
async def chat_completions(request: ChatCompletionRequest):
    try:
        # 1. Extract messages dicts
        msgs = [m.dict() for m in request.messages]

        # 2. Get generation from engine
        response_text = await engine.generate_judgment(msgs)

        # 3. Format as OpenAI JSON (Required by Ragas)
        return {
            "id": "chatcmpl-judge",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_text
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": 0, # Optional for Ragas
                "completion_tokens": 0,
                "total_tokens": 0
            }
        }
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # Run on a different port (e.g., 8002) to avoid conflict with your Answer Generator (8001)
    print("⚖️ Starting Judge Service (Bearer Auth) on Port 8002")
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)