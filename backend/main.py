from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Allow requests from your React dev server (localhost:5173)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # use ["http://localhost:5173"] in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/hello")
def hello_from_backend():
    return {"message": "Hello from backend"}
