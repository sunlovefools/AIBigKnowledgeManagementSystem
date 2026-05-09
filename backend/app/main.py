import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()  # Need to be called before the local import below

from app.mcp.server import mcp_asgi_app, rag_mcp


def configure_third_party_logging() -> None:
    """Reduce noisy INFO logs from SDK internals."""
    noisy_loggers = (
        "astrapy",
        "astrapy.data.cursors.cursor",
        "httpx",
        "httpcore",
    )
    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


configure_third_party_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ = app
    async with rag_mcp.session_manager.run():
        yield


app = FastAPI(lifespan=lifespan)

# Local imports
import app.api.router_agent as agent_router
import app.api.router_auth as auth_router  # ADDED: uncommented now that auth is implemented
import app.api.router_collections as collections_router
import app.api.router_conversations as conversations_router
import app.api.router_ingest as ingest_router
import app.api.router_modifications as modifications_router
import app.api.router_query as query_router
import app.api.router_retrieve as retrieve_router

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,https://sunlovefools.github.io",
    ).split(",")
    if origin.strip()
]

# Allow requests from your React dev server and deployed frontend.
# When allow_credentials=True, you must specify explicit origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)

# --- Router Registration ---

# Authentication router  # ADDED: uncommented now that auth is implemented
app.include_router(
    auth_router.router,
    prefix="/auth",
    tags=["Authentication"],
)

# Ingestion router
app.include_router(
    ingest_router.router,
    prefix="/ingest",
    tags=["Ingestion"],
)

# Query router
app.include_router(
    query_router.router,
    prefix="/api",
    tags=["Query"],
)

# Conversations router
app.include_router(
    conversations_router.router,
    prefix="/api",
    tags=["Conversations"],
)

# Modifications router
app.include_router(
    modifications_router.router,
    prefix="/api/modifications",
    tags=["Modifications"],
)

# Retrieve router
app.include_router(
    retrieve_router.router,
    prefix="/api/retrieve",
    tags=["Retrieve"],
)

# Agent router
app.include_router(
    agent_router.router,
    prefix="/api/agent",
    tags=["Agent"],
)

# Collections router
app.include_router(
    collections_router.router,
    prefix="/api/collections",
    tags=["Collections"],
)

app.mount("/api/mcp", mcp_asgi_app)


# --- Data Models ---
class QueryRequest(BaseModel):
    """Request model for testing backend query endpoint."""

    query: str


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
