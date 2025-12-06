from fastapi import FastAPI
import httpx
import os
from dotenv import load_dotenv
from pydantic import BaseModel
import app.api.router_auth as auth_router
import app.api.router_ingest as ingest_router
from fastapi.middleware.cors import CORSMiddleware
from app.service.beam_client import query_llm
from app.service.vectordb_init import init_vector_db
import app.api.router_query as query_router
# Initialize FastAPI app
app = FastAPI()

# Initialize the vector database
@app.on_event("startup")
def startup_event():
    print("Initialising vector database (if not already created)...")
    init_vector_db()
load_dotenv()

BEAM_LLM_URL = os.getenv("BEAM_LLM_URL")
BEAM_LLM_KEY = os.getenv("BEAM_LLM_KEY")

# TEST FOR SENDINGQUERY TO BACKEND
class QueryRequest(BaseModel):
    query: str
# TEST FOR SENDING WUERY TO BACKEND

# Allow requests from your React dev server (localhost:5173)
# When allow_credentials=True, you must specify explicit origins (can't use "*")
# explain about the line below
# This is a security measure to prevent unauthorized domains from accessing your API
app.add_middleware(
    CORSMiddleware,
    # This is a security measure to prevent unauthorized domains from accessing our API
    allow_origins=["*"],  # dev origins, during production, specify your frontend domain here, eg. ["https://myfrontend.com"]
    allow_credentials=True, # Allow cookies, authorization headers, etc in the requests to the backend
    allow_methods=["*"], # Allow all HTTP methods (GET, POST, PUT, DELETE, etc)
    allow_headers=["*"], # Allow all headers, including custom headers
    # Popular headers include Authorization, Content-Type, X-Requested-With, etc.
)

# If the request URL starts with /auth, forward it to router_auth.py
app.include_router(
   auth_router.router,     # The router object from router_auth.py
   prefix="/auth",          # All routes from this file will start with /auth
   tags=["Authentication"]  # Groups them nicely in the API docs
)
app.include_router(
    ingest_router.router,
    prefix="/ingest",
    tags=["Ingestion"]
)

app.include_router(
    query_router.router, 
    prefix="/api", 
    tags=["Query"])

# A simple test endpoint to verify the backend is running, not being used at all
@app.get("/hello")
def hello_from_backend():
    return {"message": "Hello from backend"}

@app.post("/query")
async def ask_user(body: QueryRequest):
    user_query = body.query
    print(f"Received query: {user_query}")

    return {"response": "Hi there! This is a placeholder response from the backend."}

    # headers = {
    #     "Content-Type": "application/json",
    #     "Authorization": f"Bearer {BEAM_LLM_KEY}",
    #     "Connection": "keep-alive",
    # }

    # payload = {"prompt": user_query}

    # try:
    #     async with httpx.AsyncClient() as client:
    #         response = await client.post(
    #             BEAM_LLM_URL,
    #             headers=headers,
    #             json=payload,
    #             timeout=60.0,
    #         )

    #     response.raise_for_status()
    #     data = response.json()

    #     # Your LLM output is in data["response"]
    #     llm_output = data.get("response", "(LLM returned no response field)")

    # except Exception as e:
    #     print("Error talking to Beam:", e)
    #     return {"response": "❌ Error: Could not reach LLM service"}

    # return {"response": llm_output}