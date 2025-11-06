from fastapi import FastAPI
import app.api.router_auth as auth_router # Import the auth router inside app.api
from fastapi.middleware.cors import CORSMiddleware
from app.service.beam_client import query_llm
# Initialize FastAPI app
app = FastAPI()

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

# A simple test endpoint to verify the backend is running, not being used at all
@app.get("/hello")
def hello_from_backend():
    return {"message": "Hello from backend"}

@app.post("/ask")
def ask_user(prompt: str):
    llm_response = query_llm(prompt)
    return {"answer": llm_response}