import os
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv() # Need to be called before the local import below
app = FastAPI()

# Local imports
import app.api.router_ingest as ingest_router
import app.api.router_query as query_router
import app.api.router_modifications as modifications_router
import app.api.router_retrieve as retrieve_router
import app.api.router_agent as agent_router
# import app.api.router_auth as auth_router # Uncomment when auth is implemented


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

# --- Router Registration ---

# Authentication router (commented out until implemented)
# app.include_router(
#    auth_router.router,     # The router object from router_auth.py
#    prefix="/auth",          # All routes from this file will start with /auth
#    tags=["Authentication"]  # Groups them nicely in the API docs
# )

# Ingestion router
app.include_router(
    ingest_router.router,
    prefix="/ingest",
    tags=["Ingestion"]
)

# Query router
app.include_router(
    query_router.router, 
    prefix="/api", 
    tags=["Query"])

# Modifications router
app.include_router(
    modifications_router.router,
    prefix="/api/modifications",
    tags=["Modifications"]
)

# Retrieve router
app.include_router(
    retrieve_router.router,
    prefix="/api/retrieve",
    tags=["Retrieve"]
)

# Agent router
app.include_router(
    agent_router.router,
    prefix="/api/agent",
    tags=["Agent"]
)

# --- Data Models ---
# TEST FOR SENDINGQUERY TO BACKEND
class QueryRequest(BaseModel):
    """Request model for testing backend query endpoint."""
    query: str
# TEST FOR SENDING QUERY TO BACKEND

# --- Endpoints ---
@app.get("/hello")
def hello_from_backend():
    """Simple test endpoint to verify backend is running."""
    return {"message": "Hello from backend"}

@app.post("/query")
async def ask_user(body: QueryRequest):
    """Test endpoint to receive a query and respond with a placeholder."""
    user_query = body.query
    print(f"Received query: {user_query}")

    return {"response": "Hi there! This is a placeholder response from the backend."}